import concurrent.futures
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import cli
import core
import handoff
import install
import models
import probe
import runtime

ROOT = Path(__file__).resolve().parents[1]


def profile(machine="crux"):
    return core.load_profile(ROOT / "profiles" / f"{machine}.toml")


def base(machine="crux"):
    return {
        "platform": "Linux", "architecture": "x86_64", "python_version": [3, 12, 11],
        "executable": "/site/bin/python3", "prefix": "/site", "is_venv": False,
        "loaded_modules": "", "packages": {} if machine == "crux" else {
            "torch": {"version": "2.13.0a0+site", "location": "/site/lib/site-packages"},
            "mpi4py": {"version": "4.1.2", "location": "/site/lib/site-packages"},
        },
    }


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cg-env-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def deployment(self):
        prefix = self.root / "release with 'quotes'"
        (prefix / "venv/bin").mkdir(parents=True)
        (prefix / "venv/bin/python").symlink_to(sys.executable)
        (prefix / "venv/bin/activate").write_text(":\n")
        checkpoint = self.root / "shared models/model.model"
        checkpoint.parent.mkdir()
        checkpoint.write_bytes(b"immutable model fixture")
        record = {"name": "medium-mpa-0", "path": str(checkpoint),
                  "cache_filename": "macempa0mediummodel", "sha256": core.digest(checkpoint)}
        manifest = {"schema_version": 1, "deployment_id": "test-release", "models": [record],
                    "prefix": str(prefix), "profile": profile(), "builder_uid": os.getuid(),
                    "status": "installed-unvalidated", "calculate_sha256": hashlib.sha256(b"pass\n").hexdigest()}
        core.write_json(prefix / "manifest.json", manifest)
        (prefix / "runtime.py").write_text((ROOT / "lib/runtime.py").read_text())
        (prefix / "setup.sh").write_text(install.setup_script(prefix, profile()))
        helpers = prefix / "validation-tools"
        helpers.mkdir()
        (helpers / "calculate.py").write_bytes(b"pass\n")
        (helpers / "iri_evidence.py").write_text((ROOT / "lib/iri_evidence.py").read_text())
        return prefix, manifest


class Entrypoints(Workspace):
    def test_all_six_dry_runs_are_read_only(self):
        for machine in core.MACHINES:
            for method in ("uv", "native"):
                with self.subTest(machine=machine, method=method):
                    prefix = self.root / f"{machine}-{method}"
                    command = ["bash", str(ROOT / "machines" / machine / f"install-{method}.sh"),
                               "--prefix", str(prefix), "--model-root", str(self.root / "models"), "--dry-run"]
                    result = subprocess.run(command, text=True, capture_output=True, check=True)
                    data = json.loads(result.stdout)
                    self.assertEqual(data["profile"]["machine"], machine)
                    self.assertEqual(data["method"], method)
                    self.assertFalse(prefix.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_routes_all_six_installers(self):
        for machine in core.MACHINES:
            for method in ("uv", "native"):
                with patch.object(cli, "install") as execute:
                    self.assertEqual(cli.main(["install", machine, method, "--prefix", str(self.root / "env"),
                                               "--model-root", str(self.root / "models")]), 0)
                    args = execute.call_args.args
                    self.assertEqual(args[0]["machine"], machine)
                    self.assertEqual(args[1], method)

    def test_profile_machine_mismatch(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            core.load_profile(ROOT / "profiles/aurora.toml", "polaris")

    def test_missing_lock_fails_without_creating_prefix(self):
        target = self.root / "env"
        with self.assertRaisesRegex(ValueError, "Missing prepared lock"):
            install.install(profile(), "uv", target, self.root / "models", {})
        self.assertFalse(target.exists())

    def test_relative_prefix_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            core.absolute("relative/env")

    def test_module_initialization_for_both_managers(self):
        shim = self.root / "bashenv"
        trace = self.root / "trace"
        shim.write_text('module() { echo "module $*" >> "$CG_TEST_TRACE"; }\n'
                        'conda() { echo "conda $*" >> "$CG_TEST_TRACE"; }\n')
        with patch.dict(os.environ, {"BASH_ENV": str(shim), "CG_TEST_TRACE": str(trace)}):
            for machine in ("polaris", "aurora"):
                for _ in ("uv", "native"):
                    core.run(profile(machine), [sys.executable, "-c", "pass"])
        calls = trace.read_text().splitlines()
        self.assertEqual(calls.count("module load conda/2026-10-01"), 2)
        self.assertEqual(calls.count("conda activate base"), 2)
        self.assertEqual(calls.count("module load frameworks/2026.1.0"), 2)


class LockProtection(Workspace):
    def make_lock(self, machine="polaris"):
        config = profile(machine)
        directory = self.root / f"{machine}-lock"
        directory.mkdir()
        config["lock"] = str(directory)
        (directory / "overlay.txt").write_text("chemgraph==0.7.0\n")
        data = {"schema_version": 1, "profile": core.profile_identity(config),
                "requirements_sha256": core.digest(ROOT / "requirements/base.in"),
                "overlay_sha256": core.digest(directory / "overlay.txt"),
                "overlay_packages": {"chemgraph": "0.7.0"}, "base": base(machine), "base_conflicts": []}
        core.write_json(directory / "lock.json", data)
        return config, data

    def test_tampered_lock_is_rejected(self):
        config, _ = self.make_lock()
        (Path(config["lock"]) / "overlay.txt").write_text("torch==2.8.0\n")
        with self.assertRaisesRegex(ValueError, "checksum"):
            install.load_lock(config)

    def test_protected_transitive_dependencies_are_rejected(self):
        for name in ("torch", "mpi4py", "nvidia-cublas-cu12", "triton-xpu", "intel-cmplr-lib-rt"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "protected"):
                install.reject_protected({"install": [{"metadata": {"name": name}}]}, profile("aurora"))

    def test_cpu_torch_can_be_installed_on_crux(self):
        install.reject_protected({"install": [{"metadata": {"name": "torch"}}]}, profile())

    def test_changed_base_is_rejected_before_creation(self):
        config, data = self.make_lock()
        changed = {**data["base"], "python_version": [3, 13, 0]}
        prefix = self.root / "env"
        with patch.object(install, "snapshot", return_value=changed):
            with self.assertRaisesRegex(ValueError, "Base environment changed"):
                install.install(config, "native", prefix, self.root / "models", {})
        self.assertFalse(prefix.exists())

    def test_existing_prefix_is_preserved(self):
        prefix = self.root / "env"
        prefix.mkdir()
        marker = prefix / "existing.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            install.install(profile(), "native", prefix, self.root / "models", {})
        self.assertEqual(marker.read_text(), "keep")

    def test_source_artifacts_are_pinned(self):
        commit = "a" * 40
        report = {"install": [
            {"metadata": {"name": "example"}, "download_info": {
                "url": "https://example.org/example.whl", "archive_info": {"hashes": {"sha256": "b" * 64}}}},
            {"metadata": {"name": "chemgraph"}, "download_info": {
                "url": "https://github.com/argonne-lcf/ChemGraph.git", "vcs_info": {"vcs": "git", "commit_id": commit}}},
        ]}
        result = install.report_requirements(report)
        self.assertIn("#sha256=" + "b" * 64, result)
        self.assertIn(".git@" + commit, result)

    def test_hashless_artifacts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            install.report_requirements({"install": [{"metadata": {"name": "example"},
                "download_info": {"url": "https://example.org/unknown.whl"}}]})

    def test_protected_package_origin_is_checked(self):
        expected = base("polaris")
        changed = {**expected, "packages": {**expected["packages"], "torch": {
            "version": "2.13.0a0+site", "location": "/overlay/site-packages"}}}
        with patch.object(install, "snapshot", return_value=changed):
            with self.assertRaisesRegex(ValueError, "shadowed: torch"):
                install.verify_environment(profile("polaris"), "/overlay/bin/python", expected, [])

    def test_new_dependency_conflicts_are_not_ignored(self):
        expected = base("polaris")
        with patch.object(install, "snapshot", return_value=expected), \
             patch.object(install, "run", return_value=subprocess.CompletedProcess([], 0, '{"errors": []}', "")), \
             patch.object(install, "pip_check", return_value=["old site conflict", "new conflict"]):
            with self.assertRaisesRegex(ValueError, "new conflict"):
                install.verify_environment(profile("polaris"), "/overlay/bin/python", expected, ["old site conflict"])

    def test_lock_preparation_records_only_a_successful_resolution(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                config = profile("polaris")
                output = self.root / ("rejected" if blocked else "prepared")
                reference = []
                commands = []

                def fake_run(config, command, **kwargs):
                    command = list(map(str, command))
                    commands.append(command)
                    if "venv" in command:
                        reference.append(Path(command[-1]))
                        reference[0].mkdir()
                    if "--report" in command:
                        name = "torch" if blocked else "chemgraph"
                        report_file = Path(command[command.index("--report") + 1])
                        core.write_json(report_file, {"install": [{"metadata": {"name": name}, "download_info": {
                            "url": "https://example.org/pkg.whl", "archive_info": {"hashes": {"sha256": "a" * 64}}}}]})
                    stdout = "chemgraph==0.7.0\npip==25.2\n" if "freeze" in command else ""
                    return subprocess.CompletedProcess(command, 0, stdout, "")

                def verify(*args, **kwargs):
                    return {"packages": {"chemgraph": {"version": "0.7.0", "location": str(reference[0] / "lib")}}}

                with ExitStack() as stack:
                    stack.enter_context(patch.object(install, "snapshot", return_value=base("polaris")))
                    stack.enter_context(patch.object(install, "run", side_effect=fake_run))
                    stack.enter_context(patch.object(install, "pip_check", return_value=[]))
                    stack.enter_context(patch.object(install, "verify_environment", side_effect=verify))
                    if blocked:
                        with self.assertRaisesRegex(ValueError, "protected"):
                            install.prepare_lock(config, output)
                        self.assertFalse((output / "lock.json").exists())
                        self.assertFalse(any("--no-deps" in c for c in commands))
                    else:
                        install.prepare_lock(config, output)
                        lock = core.read_json(output / "lock.json")
                        self.assertTrue(lock["prepared_only"])
                        self.assertEqual(lock["overlay_packages"], {"chemgraph": "0.7.0"})
                        self.assertEqual(lock["overlay_sha256"], core.digest(output / "overlay.txt"))
                        resolution = next(c for c in commands if "--dry-run" in c)
                        self.assertNotIn("--ignore-installed", resolution)
                self.assertFalse(reference[0].exists())

    def test_failed_install_is_marked_unpublishable(self):
        config, lock = self.make_lock()
        prefix = self.root / "failed-env"
        with patch.object(install, "snapshot", return_value=lock["base"]), \
             patch.object(install, "records", return_value=[]), \
             patch.object(install, "run", side_effect=RuntimeError("installer failed")):
            with self.assertRaisesRegex(RuntimeError, "installer failed"):
                install.install(config, "native", prefix, self.root / "models", {"mace_version": "0.3.16"})
        self.assertEqual(core.read_json(prefix / "manifest.json")["status"], "failed")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            handoff.prepare_iri(prefix, self.root / "iri")

    def test_all_six_installations_use_locked_packages(self):
        for machine in core.MACHINES:
            config, lock = self.make_lock(machine)
            for method in ("uv", "native"):
                with self.subTest(machine=machine, method=method):
                    prefix = self.root / f"{machine}-{method}"
                    commands = []
                    helper = self.root / "calculate.py"
                    helper.write_text("pass\n")

                    def fake_run(config, command, **kwargs):
                        command = list(map(str, command))
                        commands.append(command)
                        if "venv" in command:
                            (prefix / "venv/bin").mkdir(parents=True)
                        stdout = str(helper) + "\n" if "-c" in command else ""
                        return subprocess.CompletedProcess(command, 0, stdout, "")

                    installed = {"packages": {"chemgraph": {"version": "0.7.0", "location": str(prefix / "venv/lib")}}}
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(install, "snapshot", return_value=lock["base"]))
                        stack.enter_context(patch.object(install, "records", return_value=[]))
                        stack.enter_context(patch.object(install.shutil, "which", return_value="/tools/uv"))
                        stack.enter_context(patch.object(install, "run", side_effect=fake_run))
                        stack.enter_context(patch.object(install, "verify_environment", return_value=installed))
                        install.install(config, method, prefix, self.root / "models", {"mace_version": "0.3.16"})
                    self.assertEqual(core.read_json(prefix / "manifest.json")["status"], "installed-unvalidated")
                    installs = [c for c in commands if "install" in c]
                    self.assertEqual(len(installs), 1)
                    self.assertIn("--no-deps", installs[0])
                    self.assertIn(str(Path(config["lock"]) / "overlay.txt"), installs[0])
                    self.assertEqual("--system-site-packages" in commands[0], machine != "crux")
                    if method == "uv":
                        self.assertIn("--no-python-downloads", commands[0])
                        self.assertIn("copy", installs[0])
                    self.assertFalse((prefix / "target-environment.toml").exists())


class SharedModels(Workspace):
    def test_catalog_is_pinned(self):
        data = models.catalog(ROOT / "models/catalog.toml")
        self.assertEqual(data["models"][0]["cache_filename"], "macempa0mediummodel")
        self.assertEqual(len(data["models"][0]["sha256"]), 64)

    def test_local_provision_is_verified_and_repeatable(self):
        source = self.root / "source"
        source.write_bytes(b"model")
        model = {"name": "test", "sha256": core.digest(source), "cache_filename": "testmodel"}
        data = {"models": [model]}
        model_root = self.root / "models"
        first = models.provision(model_root, data, source)
        with patch.object(models.urllib.request, "urlopen", side_effect=AssertionError("Unexpected download")):
            self.assertEqual(models.provision(model_root, data), first)
        self.assertEqual(Path(first[0]["path"]).stat().st_mode & 0o777, 0o444)

    def test_bad_download_is_never_published(self):
        source = self.root / "bad"
        source.write_bytes(b"bad")
        model = {"name": "test", "sha256": "0" * 64, "cache_filename": "testmodel"}
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            models.provision(self.root / "models", {"models": [model]}, source)
        self.assertFalse(models.model_path(self.root / "models", model).exists())
        self.assertFalse((self.root / "models/.provision.lock").exists())

    def test_existing_broken_model_symlink_is_preserved(self):
        source = self.root / "source"
        source.write_bytes(b"model")
        model = {"name": "test", "sha256": core.digest(source), "cache_filename": "testmodel"}
        destination = models.model_path(self.root / "models", model)
        destination.parent.mkdir(parents=True)
        destination.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "Existing model"):
            models.provision(self.root / "models", {"models": [model]}, source)
        self.assertTrue(destination.is_symlink())

    def test_concurrent_private_cache_setup_uses_one_shared_file(self):
        _, manifest = self.deployment()
        cache_root = self.root / "user cache"
        with patch.dict(os.environ, {"CHEMGRAPH_CACHE_ROOT": str(cache_root)}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
                paths = list(workers.map(lambda _: runtime.prepare_cache(manifest), range(16)))
        self.assertEqual(len(set(paths)), 1)
        link = paths[0] / "mace/macempa0mediummodel"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), Path(manifest["models"][0]["path"]))

    def test_conflicting_user_file_is_preserved(self):
        _, manifest = self.deployment()
        with patch.dict(os.environ, {"CHEMGRAPH_CACHE_ROOT": str(self.root / "user")}):
            cache = runtime.prepare_cache(manifest)
            link = cache / "mace/macempa0mediummodel"
            link.unlink()
            link.write_text("personal data")
            with self.assertRaisesRegex(ValueError, "preserved"):
                runtime.prepare_cache(manifest)
            self.assertEqual(link.read_text(), "personal data")

    def test_strict_shell_activation_is_idempotent_for_two_cache_roots(self):
        prefix, manifest = self.deployment()
        for user in ("alice", "bob"):
            environment = {**os.environ, "CHEMGRAPH_CACHE_ROOT": str(self.root / user)}
            script = ('set -euo pipefail\nsource "$1/setup.sh"\nfirst=$XDG_CACHE_HOME\n'
                      'source "$1/setup.sh"\n[[ "$first" == "$XDG_CACHE_HOME" ]]\n'
                      '[[ $- == *u* ]]\nprintf "%s\\n" "$XDG_CACHE_HOME"\n')
            result = subprocess.run(["bash", "-c", script, "test", str(prefix)], env=environment,
                                    text=True, capture_output=True, check=True)
            link = Path(result.stdout.strip()) / "mace/macempa0mediummodel"
            self.assertEqual(link.resolve(), Path(manifest["models"][0]["path"]))

    def test_unavailable_shared_model_fails_activation(self):
        prefix, manifest = self.deployment()
        Path(manifest["models"][0]["path"]).unlink()
        result = subprocess.run(["bash", "-c", 'set -euo pipefail; source "$1/setup.sh"', "test", str(prefix)],
                                env={**os.environ, "CHEMGRAPH_CACHE_ROOT": str(self.root / "user")},
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cache setup failed", result.stderr)


class Handoff(Workspace):
    def evidence(self):
        prefix, manifest = self.deployment()
        common = {"status": "passed", "manifest_sha256": core.digest(prefix / "manifest.json"),
                  "deployment_id": manifest["deployment_id"]}
        compute = self.root / "compute.json"
        access = self.root / "access.json"
        iri = self.root / "iri"
        iri.mkdir()
        core.write_json(compute, {**common, "kind": "compute", "device": "cpu", "pbs_job_id": "1.pbs",
            "downloads_disabled": True, "calculations": {name: {} for name in ("emt", "tblite", "mace-default", "mace-named")}})
        core.write_json(access, {**common, "kind": "access", "uid": os.getuid() + 1})
        core.write_json(iri / "result.json", {"success": True, "converged": True, "potential_energy": -1.2})
        core.write_json(iri / "acceptance.json", {**common, "kind": "iri", "pbs_job_id": "2.pbs",
            "calculate_sha256": manifest["calculate_sha256"], "result_sha256": core.digest(iri / "result.json")})
        return prefix, compute, access, iri

    def test_published_toml_quotes_paths_and_preserves_model_names(self):
        prefix, compute, access, iri = self.evidence()
        output = self.root / "handoff"
        handoff.publish(prefix, compute, access, iri, "iri-opaque-id", output)
        data = tomllib.loads((output / "target-environment.toml").read_text())
        self.assertEqual(shlex.split(data["environment_setup"]), ["source", str(prefix / "setup.sh")])
        self.assertEqual(data["python"], str(prefix / "venv/bin/python"))
        self.assertEqual(core.read_json(output / "calculators.json")["mace"]["model"], "medium-mpa-0")

    def test_publish_requires_a_different_account(self):
        prefix, compute, access, iri = self.evidence()
        data = core.read_json(access)
        data["uid"] = os.getuid()
        core.write_json(access, data)
        with self.assertRaisesRegex(ValueError, "second authorized"):
            handoff.publish(prefix, compute, access, iri, "iri-id", self.root / "out")

    def test_publish_rejects_stale_reports(self):
        prefix, compute, access, iri = self.evidence()
        data = core.read_json(compute)
        data["manifest_sha256"] = "changed"
        core.write_json(compute, data)
        with self.assertRaisesRegex(ValueError, "stale"):
            handoff.publish(prefix, compute, access, iri, "iri-id", self.root / "out")

    def test_publish_rejects_changed_results(self):
        prefix, compute, access, iri = self.evidence()
        (iri / "result.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "IRI calculation evidence"):
            handoff.publish(prefix, compute, access, iri, "iri-id", self.root / "out")

    def test_iri_bundle_preserves_helper_and_records_real_execution(self):
        prefix, manifest = self.deployment()
        bundle = self.root / "iri run"
        handoff.prepare_iri(prefix, bundle)
        self.assertEqual(core.digest(bundle / "calculate.py"), manifest["calculate_sha256"])
        subprocess.run(["bash", "-n", str(bundle / "launch.sh")], check=True)
        nodefile = self.root / "nodes"
        nodefile.write_text(socket.gethostname() + "\n")
        env = {**os.environ, "CHEMGRAPH_DEPLOYMENT": str(prefix), "PBS_JOBID": "123.pbs", "PBS_NODEFILE": str(nodefile)}
        command = [sys.executable, str(bundle / "acceptance.py")]
        subprocess.run([*command, "before"], cwd=bundle, env=env, check=True)
        core.write_json(bundle / "result.json", {"success": True, "converged": True, "potential_energy": -2.5})
        subprocess.run([*command, "after"], cwd=bundle, env=env, check=True)
        self.assertEqual(core.read_json(bundle / "acceptance.json")["pbs_job_id"], "123.pbs")
        repeated = subprocess.run([*command, "before"], cwd=bundle, env=env, capture_output=True)
        self.assertNotEqual(repeated.returncode, 0)


class DependencyAudit(unittest.TestCase):
    def test_missing_transitive_extra_and_version_conflicts_are_detected(self):
        class Distribution:
            def __init__(self, version, requires=()):
                self.version, self.requires = version, requires

        packages = {
            "chemgraph": Distribution("0.7.0", ['tblite==0.4.0; extra == "calculators"', "mace-torch>=0.3.16"]),
            "mace-torch": Distribution("0.3.16", ["e3nn<0.5"]),
            "e3nn": Distribution("0.5.1"),
        }
        with patch.object(probe, "distributions", return_value=packages):
            result = probe.audit()
        self.assertTrue(any("tblite" in error for error in result["errors"]))
        self.assertTrue(any("e3nn" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
