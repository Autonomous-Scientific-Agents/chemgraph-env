"""Staged beside the unchanged ChemGraph calculate.py for IRI acceptance."""

import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    expected = json.loads(Path("expected.json").read_text())
    prefix = Path(os.environ["CHEMGRAPH_DEPLOYMENT"])
    if sha(prefix / "manifest.json") != expected["manifest_sha256"]:
        raise ValueError("Deployment manifest changed")
    if sha("calculate.py") != expected["calculate_sha256"]:
        raise ValueError("calculate.py differs from the installed ChemGraph helper")
    params = json.loads(Path("input.json").read_text())
    if params.get("calculator", {}).get("calculator_type") != expected["calculator_type"]:
        raise ValueError("Input calculator differs from the acceptance configuration")
    job = os.environ.get("PBS_JOBID")
    nodes = {h.split(".")[0] for h in Path(os.environ["PBS_NODEFILE"]).read_text().split()}
    if not job or socket.gethostname().split(".")[0] not in nodes:
        raise ValueError("IRI acceptance must run on an allocated compute node")
    evidence = {**expected, "pbs_job_id": job, "hostname": socket.gethostname()}
    if sys.argv[1] == "before":
        if Path("started.json").exists() or Path("result.json").exists():
            raise ValueError("Acceptance run already has execution evidence; use a fresh run")
        Path("started.json").write_text(json.dumps(evidence, indent=2) + "\n")
        return
    started = json.loads(Path("started.json").read_text())
    if started != evidence:
        raise ValueError("Acceptance job changed between start and completion")
    result = json.loads(Path("result.json").read_text())
    if result.get("simulation_input", {}).get("calculator", {}).get("calculator_type") != expected["calculator_type"]:
        raise ValueError("Result calculator differs from the acceptance configuration")
    energy = result.get("potential_energy")
    if (result.get("success") is not True or result.get("converged") is not True
            or isinstance(energy, bool) or not isinstance(energy, (int, float))
            or not math.isfinite(energy)):
        raise ValueError("ChemGraph result did not pass scientific acceptance")
    evidence.update(status="passed", kind="iri", result_sha256=sha("result.json"))
    Path("acceptance.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
