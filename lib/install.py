"""Prepare site-dependent locks and install from them without resolving again."""

import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile

from core import (
    LIB, ROOT, assert_base_matches, check_base, digest, module_setup, pip_check,
    profile_identity, protected, read_json, run, snapshot, write_json,
)
from models import records


def reject_protected(report, profile):
    if profile["system_site_packages"]:
        blocked = [p["metadata"]["name"] for p in report["install"]
                   if protected(p["metadata"]["name"])]
        if blocked:
            raise ValueError("Resolution would shadow protected site packages: " + ", ".join(blocked))


def report_requirements(report):
    """Pin the resolver's exact artifacts so installation cannot re-resolve."""
    lines = []
    for package in report["install"]:
        name = package["metadata"]["name"]
        info = package["download_info"]
        url = info["url"]
        if "vcs_info" in info:
            vcs = info["vcs_info"]
            if vcs["vcs"] != "git" or not re.fullmatch(r"[0-9a-f]{40}", vcs["commit_id"]):
                raise ValueError("Only Git sources pinned to full commits are supported")
            url = f"git+{url}@{vcs['commit_id']}"
        else:
            sha = info.get("archive_info", {}).get("hashes", {}).get("sha256")
            if not sha:
                raise ValueError(f"No SHA-256 supplied for {name}")
            url = url.split("#")[0] + f"#sha256={sha}"
        lines.append(f"{name} @ {url}")
    return "\n".join(lines) + "\n"


def verify_environment(profile, python, base, baseline, log=None):
    actual = snapshot(profile, python, log)
    if profile["system_site_packages"]:
        for name, package in base["packages"].items():
            if protected(name) and actual["packages"].get(name) != package:
                raise ValueError(f"Site package was changed or shadowed: {name}")
    check = run(profile, [python, LIB / "probe.py", "audit"], log=log,
                capture=True, check=False)
    if check.returncode:
        raise ValueError("ChemGraph dependency audit failed:\n" + check.stdout + check.stderr)
    new_conflicts = set(pip_check(profile, python, log)) - set(baseline)
    if new_conflicts:
        raise ValueError("New dependency conflicts:\n" + "\n".join(sorted(new_conflicts)))
    return actual


def prepare_lock(profile, output):
    base = snapshot(profile)
    check_base(profile, base)
    output.mkdir(parents=True, exist_ok=False)
    print(f"Preparing a reference environment; log: {output / 'prepare.log'}", flush=True)
    with (output / "prepare.log").open("w") as log:
        # A temporary reference environment is never moved or published.
        with tempfile.TemporaryDirectory(prefix="chemgraph-lock-") as work:
            work = Path(work)
            reference = work / "reference"
            command = [profile["python"], "-m", "venv"]
            if profile["system_site_packages"]:
                command.append("--system-site-packages")
            run(profile, [*command, reference], log=log)
            python = reference / "bin/python"
            run(profile, [python, "-m", "pip", "install", "pip==25.2"], log=log)
            baseline = pip_check(profile, python, log)
            constraints = work / "protected.txt"
            constraints.write_text("".join(
                f"{name}=={item['version']}\n" for name, item in base["packages"].items()
                if profile["system_site_packages"] and protected(name)
            ))
            report_file = work / "report.json"
            command = [python, "-m", "pip", "install", "--dry-run", "--report", report_file,
                       "-r", ROOT / "requirements/base.in", "-c", constraints]
            if profile["device"] == "cpu":
                command += [f"torch=={profile['cpu_torch']}", "--extra-index-url",
                            "https://download.pytorch.org/whl/cpu"]
            run(profile, command, log=log)
            report = read_json(report_file)
            reject_protected(report, profile)
            resolved = work / "resolved.txt"
            resolved.write_text(report_requirements(report))
            run(profile, [python, "-m", "pip", "install", "--no-deps", "-r", resolved], log=log)
            installed = verify_environment(profile, python, base, baseline, log)
            freeze = run(profile, [python, "-m", "pip", "freeze", "--all", "--local"],
                         capture=True, log=log).stdout
            (output / "overlay.txt").write_text(freeze)
            # lock.json is the completion marker. An interrupted preparation
            # leaves a log, but cannot be mistaken for a usable lock.
            write_json(output / "lock.json", {
                "schema_version": 1,
                "profile": profile_identity(profile),
                "base": base,
                "base_conflicts": baseline,
                "requirements": (ROOT / "requirements/base.in").read_text(),
                "requirements_sha256": digest(ROOT / "requirements/base.in"),
                "overlay_sha256": digest(output / "overlay.txt"),
                "overlay_packages": {
                    name: info["version"] for name, info in installed["packages"].items()
                    if Path(info["location"]).is_relative_to(reference)
                },
                "prepared_only": True,
            })
    print(f"Lock prepared: {output}\nReview and commit lock.json and overlay.txt; compute/IRI validation is still required.")


def load_lock(profile):
    directory = Path(profile["lock"])
    if not (directory / "lock.json").is_file():
        raise ValueError(f"Missing prepared lock: {directory}. Run scripts/prepare-lock.py on {profile['machine']} first.")
    lock = read_json(directory / "lock.json")
    if lock.get("schema_version") != 1 or lock.get("profile") != profile_identity(profile):
        raise ValueError("Lock does not match this profile; prepare a new lock")
    if lock["requirements_sha256"] != digest(ROOT / "requirements/base.in"):
        raise ValueError("Common requirements changed; prepare a new lock")
    if lock["overlay_sha256"] != digest(directory / "overlay.txt"):
        raise ValueError("Overlay requirements checksum does not match the lock")
    if profile["system_site_packages"] and any(protected(n) for n in lock["overlay_packages"]):
        raise ValueError("Overlay lock contains protected site packages")
    return lock


def setup_script(prefix, profile):
    q = shlex.quote
    # Run module initialization with nounset disabled, then restore the caller's
    # setting. Explicit returns also work when sourced from an `if` condition.
    return f'''#!/bin/bash
# Source this file; do not run it as a separate process.
_chemgraph_setup_inner() {{
    if declare -F deactivate >/dev/null; then deactivate || return; fi
    unset PYTHONPATH PYTHONHOME
    export PYTHONNOUSERSITE=1
    {module_setup(profile) or ':'}
    source {q(str(prefix / 'venv/bin/activate'))} || return
    if [[ -z "${{CHEMGRAPH_CACHE_ROOT:-}}" ]]; then
        CHEMGRAPH_CACHE_ROOT="${{XDG_CACHE_HOME:-$HOME/.cache}}/chemgraph"
    fi
    export CHEMGRAPH_CACHE_ROOT
    local cache
    cache=$({q(str(prefix / 'venv/bin/python'))} {q(str(prefix / 'runtime.py'))} {q(str(prefix / 'manifest.json'))}) || return
    export XDG_CACHE_HOME="$cache"
    export CHEMGRAPH_DEPLOYMENT={q(str(prefix))}
}}
_chemgraph_setup() {{
    local had_nounset=0 rc=0
    case $- in *u*) had_nounset=1; set +u ;; esac
    if _chemgraph_setup_inner; then rc=0; else rc=$?; fi
    if [[ "$had_nounset" == 1 ]]; then set -u; fi
    return "$rc"
}}
if _chemgraph_setup; then
    unset -f _chemgraph_setup _chemgraph_setup_inner
else
    unset -f _chemgraph_setup _chemgraph_setup_inner
    return 1
fi
'''


def install(profile, method, prefix, model_root, data):
    if prefix.exists():
        raise ValueError(f"Deployment already exists; choose a new versioned prefix: {prefix}")
    lock = load_lock(profile)
    base = snapshot(profile)
    check_base(profile, base)
    assert_base_matches(lock["base"], base)
    model_records = records(model_root, data)
    if method == "uv" and shutil.which("uv") is None:
        raise ValueError("uv is required; install it before running this script")
    prefix.mkdir(parents=True, exist_ok=False)
    print(f"Installing {profile['machine']} with {method}; log: {prefix / 'install.log'}", flush=True)
    manifest = {
        "schema_version": 1,
        "status": "installing",
        "prefix": str(prefix),
        "profile": profile_identity(profile),
        "method": method,
        "builder_uid": os.getuid(),
        "deployment_id": f"{profile['machine']}-{method}-" + hashlib.sha256(
            (str(prefix) + lock["overlay_sha256"]).encode()).hexdigest()[:16],
        "models": model_records,
        "mace_version": data["mace_version"],
        "lock": lock,
        "toolkit_files": {
            str(path.relative_to(ROOT)): digest(path)
            for path in sorted((ROOT / "lib").glob("*.py"))
        },
        "validation": {},
    }
    write_json(prefix / "manifest.json", manifest)
    try:
        with (prefix / "install.log").open("w") as log:
            venv = prefix / "venv"
            if method == "uv":
                # Bind the executable before a module can change PATH.
                uv = shutil.which("uv")
                command = [uv, "venv", "--python", base["executable"], "--no-python-downloads"]
            else:
                command = [base["executable"], "-m", "venv"]
            if profile["system_site_packages"]:
                command.append("--system-site-packages")
            run(profile, [*command, venv], log=log)
            python = venv / "bin/python"
            requirements = Path(profile["lock"]) / "overlay.txt"
            if method == "uv":
                command = [uv, "pip", "install", "--python", python, "--link-mode", "copy"]
            else:
                command = [python, "-m", "pip", "install"]
            run(profile, [*command, "--no-deps", "-r", requirements], log=log)
            version_command = [uv, "--version"] if method == "uv" else [python, "-m", "pip", "--version"]
            manifest["installer_version"] = run(profile, version_command, capture=True, log=log).stdout.strip()
            installed = verify_environment(profile, python, base, lock["base_conflicts"], log)
            for name, version in lock["overlay_packages"].items():
                actual = installed["packages"].get(name, {})
                if actual.get("version") != version or not Path(actual["location"]).is_relative_to(venv):
                    raise ValueError(f"Overlay package does not match the lock: {name}")
            manifest["installed"] = installed
            manifest["status"] = "installed-unvalidated"
            shutil.copyfile(ROOT / "lib/runtime.py", prefix / "runtime.py")
            (prefix / "setup.sh").write_text(setup_script(prefix, profile))
            (prefix / "setup.sh").chmod(0o644)
            # Keep verification helpers with the deployment so later checks do
            # not accidentally use a different toolkit revision.
            helpers = prefix / "validation-tools"
            helpers.mkdir()
            for path in (ROOT / "lib").glob("*.py"):
                shutil.copyfile(path, helpers / path.name)
            calculate_path = run(profile, [python, "-c", (
                "from pathlib import Path; import chemgraph.skills; "
                "print(Path(chemgraph.skills.__file__).parent / 'chemgraph/assets/calculate.py')"
            )], capture=True, log=log).stdout.strip()
            shutil.copyfile(calculate_path, helpers / "calculate.py")
            manifest["calculate_sha256"] = digest(helpers / "calculate.py")
            shutil.copyfile(requirements, prefix / "overlay.txt")
            write_json(prefix / "manifest.json", manifest)
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        write_json(prefix / "manifest.json", manifest)
        raise
    print(f"Installed at {prefix}\nNot published: run compute, second-user, and IRI acceptance checks first.")
