"""Prepare an IRI acceptance run and publish a reviewed deployment's paths."""

import json
import math
import shlex
import shutil
import subprocess

from core import digest, read_json, write_json
from calculators import REQUIRED_CALCULATIONS, examples


def deployment(prefix):
    manifest = read_json(prefix / "manifest.json")
    if manifest.get("status") != "installed-unvalidated" or manifest.get("prefix") != str(prefix):
        raise ValueError("Deployment is incomplete or has moved")
    return manifest


def validate(prefix, kind, output):
    deployment(prefix)
    script = ('set -eo pipefail\nsource "$1/setup.sh"\n'
              'exec "$1/venv/bin/python" "$1/validation-tools/validation.py" "$2" '
              '--prefix "$1" --output "$3"\n')
    subprocess.run(["bash", "--noprofile", "--norc", "-c", script, "cg-validate",
                    str(prefix), kind, str(output)], check=True)


def prepare_iri(prefix, output, calculator_type="mace_polar"):
    manifest = deployment(prefix)
    if calculator_type not in {"mace_mp", "mace_polar"}:
        raise ValueError("IRI acceptance requires mace_mp or mace_polar")
    if output.is_relative_to(prefix):
        raise ValueError("Acceptance output must be outside the shared environment")
    output.mkdir(parents=True, exist_ok=False)
    for source, target in [("calculate.py", "calculate.py"), ("iri_evidence.py", "acceptance.py")]:
        shutil.copyfile(prefix / "validation-tools" / source, output / target)
    if digest(output / "calculate.py") != manifest["calculate_sha256"]:
        raise ValueError("Installed calculation helper changed")
    write_json(output / "expected.json", {
        "schema_version": 1, "deployment_id": manifest["deployment_id"],
        "manifest_sha256": digest(prefix / "manifest.json"),
        "calculate_sha256": manifest["calculate_sha256"],
        "calculator_type": calculator_type,
    })
    (output / "structure.xyz").write_text(
        "3\nWater for MACE acceptance\nO 0.0 0.0 0.0\nH 0.0 0.757 0.586\nH 0.0 -0.757 0.586\n"
    )
    profile = manifest["profile"]
    write_json(output / "input.json", {
        "input_structure_file": "structure.xyz", "output_results_file": "result.json",
        "driver": "energy", "calculator": {"calculator_type": calculator_type,
            "device": profile["device"], "default_dtype": profile["dtype"]},
    })
    python = shlex.quote(str(prefix / "venv/bin/python"))
    (output / "launch.sh").write_text(
        '#!/bin/bash\nset -eo pipefail\n'
        'RUN_DIR=$(cd "$(dirname "$0")" && pwd)\n'
        f'source {shlex.quote(str(prefix / "setup.sh"))}\n'
        'set -u\ncd "$RUN_DIR"\nexport CHEMGRAPH_LOG_DIR="$RUN_DIR" TMPDIR=/tmp\n'
        f'{python} acceptance.py before\n{python} calculate.py\n{python} acceptance.py after\n'
    )
    print(f"Prepared {output}. Stage and submit it using ChemGraph's existing IRI skill.")


def publish(prefix, compute_file, access_file, iri_run, iri_job_id, output):
    manifest = deployment(prefix)
    if output.is_relative_to(prefix):
        raise ValueError("Publication output must be outside the shared environment")
    expected = digest(prefix / "manifest.json")
    compute = read_json(compute_file)
    access = read_json(access_file)
    iri = read_json(iri_run / "acceptance.json")
    for kind, report in [("compute", compute), ("access", access), ("iri", iri)]:
        if (report.get("status") != "passed" or report.get("kind") != kind
                or report.get("manifest_sha256") != expected
                or report.get("deployment_id") != manifest["deployment_id"]):
            raise ValueError(f"Missing, stale, or failed {kind} acceptance")
    if access.get("uid") is None or access["uid"] == manifest["builder_uid"]:
        raise ValueError("Access report must be from a second authorized account")
    if (compute.get("device") != manifest["profile"]["device"]
            or not compute.get("pbs_job_id") or compute.get("downloads_disabled") is not True
            or set(compute.get("calculations", {})) != REQUIRED_CALCULATIONS):
        raise ValueError("Compute report does not cover the required acceptance tests")
    for name, calculation in compute["calculations"].items():
        energy = calculation.get("energy_eV")
        if (calculation.get("forces_finite") is not True or isinstance(energy, bool)
                or not isinstance(energy, (int, float)) or not math.isfinite(energy)):
            raise ValueError(f"Missing energy/force acceptance for {name}")
        if name.startswith("mace-polar") and calculation.get("dipole_finite") is not True:
            raise ValueError(f"Missing dipole acceptance for {name}")
    if iri.get("calculator_type") != "mace_polar":
        raise ValueError("Publishing requires an IRI MACE-Polar acceptance run")
    if (not iri.get("pbs_job_id") or iri.get("calculate_sha256") != manifest["calculate_sha256"]
            or digest(iri_run / "result.json") != iri.get("result_sha256")):
        raise ValueError("IRI calculation evidence does not match its results")
    result = read_json(iri_run / "result.json")
    if result.get("simulation_input", {}).get("calculator", {}).get("calculator_type") != "mace_polar":
        raise ValueError("IRI results are not from the required MACE-Polar calculator")
    energy = result.get("potential_energy")
    if (result.get("success") is not True or result.get("converged") is not True
            or isinstance(energy, bool) or not isinstance(energy, (int, float))
            or not math.isfinite(energy)):
        raise ValueError("IRI results did not pass scientific acceptance")
    if not iri_job_id.strip() or "\n" in iri_job_id:
        raise ValueError("Supply the full accepted IRI job ID")
    output.mkdir(parents=True, exist_ok=False)
    # IRI and PBS IDs can differ. Maintainers attest the IRI ID after collecting
    # this run with the IRI skill; the toolkit does not manage authentication.
    write_json(output / "release.json", {
        "manifest_sha256": expected, "deployment_id": manifest["deployment_id"],
        "prefix": str(prefix), "iri_job_id": iri_job_id,
        "iri_transport_attested_by_maintainer": True,
        "validation": {"compute": compute, "access": access, "iri": iri},
    })
    (output / "target-environment.toml").write_text(
        "# Validated remote environment; merge these two fields into target.toml.\n"
        + "environment_setup = " + json.dumps("source " + shlex.quote(str(prefix / "setup.sh"))) + "\n"
        + "python = " + json.dumps(str(prefix / "venv/bin/python")) + "\n"
    )
    write_json(output / "calculators.json", examples(manifest["profile"]))
    print(f"Validated handoff written to {output}. Copy its environment fields into ChemGraph skills.")
