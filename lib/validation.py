"""Compute-node and second-account acceptance checks, run in the deployment."""

import argparse
from datetime import datetime, timezone
import getpass
import json
import math
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

from core import assert_base_matches, digest, read_json, snapshot, write_json
from install import verify_environment
from runtime import prepare_cache


def allocation():
    job = os.environ.get("PBS_JOBID")
    nodefile = os.environ.get("PBS_NODEFILE")
    if not job or not nodefile:
        raise ValueError("Compute validation requires a PBS allocation")
    hosts = {host.split(".")[0] for host in Path(nodefile).read_text().split()}
    if socket.gethostname().split(".")[0] not in hosts:
        raise ValueError("This host is not in the PBS allocation")
    return job


def check_models(manifest):
    cache = prepare_cache(manifest)
    if Path(os.environ.get("XDG_CACHE_HOME", "")) != cache:
        raise ValueError("Run validation through the deployment's setup.sh")
    for model in manifest["models"]:
        if digest(model["path"]) != model["sha256"]:
            raise ValueError(f"Shared model checksum changed: {model['name']}")
        link = cache / "mace" / model["cache_filename"]
        if link.resolve() != Path(model["path"]).resolve():
            raise ValueError(f"Model cache is not linked to shared weights: {link}")
    return cache


def scientific_success(path):
    data = read_json(path)
    energy = data.get("potential_energy")
    if (data.get("success") is not True or data.get("converged") is not True
            or isinstance(energy, bool) or not isinstance(energy, (int, float))
            or not math.isfinite(energy)):
        raise ValueError(f"Calculation failed scientific acceptance: {path}")
    return data


def compute(manifest, output):
    job_id = allocation()
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    import importlib.metadata as metadata
    import numpy as np
    import torch
    from ase import Atoms
    from ase.build import molecule
    from ase.io import write
    from chemgraph.schemas.ase_input import ASEInputSchema
    from chemgraph.tools.ase_core import load_calculator, run_ase_core
    from mace.calculators.foundations_models import download_mace_mp_checkpoint, mace_mp_urls

    torch.set_num_threads(1)
    profile = manifest["profile"]
    base = snapshot(profile, manifest["lock"]["base"]["executable"])
    assert_base_matches(manifest["lock"]["base"], base)
    verify_environment(profile, sys.executable, base, manifest["lock"]["base_conflicts"])
    if metadata.version("mace-torch") != manifest["mace_version"]:
        raise ValueError("MACE version differs from the cache contract")
    if profile["system_site_packages"]:
        origin = Path(base["packages"]["torch"]["location"])
        if not Path(torch.__file__).resolve().is_relative_to(origin):
            raise ValueError("Imported torch is not the site's PyTorch")
    device = profile["device"]
    if device == "cpu":
        if torch.version.cuda is not None:
            raise ValueError("Crux must use CPU-only PyTorch")
    elif not getattr(torch, device).is_available():
        raise ValueError(f"Required accelerator is unavailable: {device}")
    torch.ones(2, device=device).sum().item()
    results = {}
    os.environ["CHEMGRAPH_LOG_DIR"] = str(output)
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    # Any attempted download fails, even if the compute node has a proxy.
    with patch("socket.socket.connect", side_effect=RuntimeError("Network disabled for acceptance")), \
         patch("urllib.request.urlopen", side_effect=RuntimeError("Model downloads disabled")):
        for model in manifest["models"]:
            if mace_mp_urls.get(model["name"]) != model["mace_url"]:
                raise ValueError("Pinned MACE model URL changed; update the cache contract")
            for requested in (None, model["name"]):
                located = Path(download_mace_mp_checkpoint(requested))
                if digest(located) != model["sha256"]:
                    raise ValueError("MACE selected a different checkpoint")
        calculators = {
            "emt": {"calculator_type": "emt"},
            "tblite": {"calculator_type": "tblite", "method": "GFN2-xTB"},
            "mace-default": {"calculator_type": "mace_mp", "device": device,
                             "default_dtype": profile["dtype"]},
            "mace-named": {"calculator_type": "mace_mp", "model": "medium-mpa-0",
                           "device": device, "default_dtype": profile["dtype"]},
        }
        for name, calculator in calculators.items():
            atoms = Atoms("Cu2", positions=[[0, 0, 0], [2.4, 0, 0]]) if name == "emt" else molecule("H2O")
            structure = output / f"{name}.xyz"
            result_file = output / f"{name}.json"
            write(structure, atoms)
            params = ASEInputSchema(input_structure_file=str(structure),
                                    output_results_file=str(result_file), driver="energy",
                                    calculator=calculator)
            response = run_ase_core(params)
            if response.get("status") != "success":
                raise ValueError(f"ChemGraph failed for {name}: {response}")
            data = scientific_success(result_file)
            atoms.calc = load_calculator(calculator)[0]
            forces = atoms.get_forces()
            if forces.shape != (len(atoms), 3) or not np.isfinite(forces).all():
                raise ValueError(f"Invalid forces from {name}")
            results[name] = {"energy_eV": data["potential_energy"], "forces_finite": True}
    if not math.isclose(results["mace-default"]["energy_eV"], results["mace-named"]["energy_eV"],
                        rel_tol=1e-5, abs_tol=1e-4):
        raise ValueError("Named and default MACE model results disagree")
    return {"pbs_job_id": job_id, "device": device, "torch_version": torch.__version__,
            "torch_origin": torch.__file__, "calculations": results, "downloads_disabled": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["compute", "access"])
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.prefix / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("status") != "installed-unvalidated":
        raise ValueError("Deployment installation did not complete")
    if args.output.resolve().is_relative_to(args.prefix.resolve()):
        raise ValueError("Acceptance output must be outside the shared environment")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "kind": args.kind, "status": "failed",
              "manifest_sha256": digest(manifest_path), "deployment_id": manifest["deployment_id"],
              "uid": os.getuid(), "user": getpass.getuser(), "hostname": socket.gethostname(),
              "time": datetime.now(timezone.utc).isoformat()}
    try:
        if args.kind == "compute":
            allocation()
        report["cache"] = str(check_models(manifest))
        if args.kind == "compute":
            report.update(compute(manifest, args.output))
        else:
            if os.getuid() == manifest["builder_uid"]:
                raise ValueError("Access acceptance must be run by a second authorized account")
            shared_paths = [args.prefix, args.prefix / "manifest.json", args.prefix / "setup.sh"]
            shared_paths.extend(Path(model["path"]) for model in manifest["models"])
            if any(os.access(path, os.W_OK) for path in shared_paths):
                raise ValueError("Ordinary users must not be able to modify the shared deployment or models")
            import chemgraph
            report["chemgraph_origin"] = chemgraph.__file__
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        write_json(args.output / "report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
