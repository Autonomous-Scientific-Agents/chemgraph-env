"""Site configuration and subprocess execution; no third-party dependencies."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
LIB = Path(__file__).resolve().parent
MACHINES = {"crux": "cpu", "polaris": "cuda", "aurora": "xpu"}
PROTECTED = (
    "torch", "torchvision", "torchaudio", "torch-*", "torchao", "torchcodec",
    "torchcomms", "torchdata", "torchtune", "triton", "triton-*", "pytorch-triton*",
    "intel-extension-for-pytorch", "oneccl*", "mpi4py", "nvidia-*", "intel-*",
    "mkl*", "oneccl", "onemkl*", "dpcpp*", "tbb*", "impi*", "umf*",
)


def canonical(name):
    import re
    return re.sub(r"[-_.]+", "-", name).lower()


def protected(name):
    return any(fnmatch.fnmatchcase(canonical(name), pattern) for pattern in PROTECTED)


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"An absolute path is required: {value}")
    if any(c in str(path) for c in "\n\r\0"):
        raise ValueError("Paths cannot contain newlines or NULs")
    return path.resolve()


def load_profile(path, machine=None):
    path = Path(path).resolve()
    with path.open("rb") as handle:
        profile = tomllib.load(handle)
    required = {
        "machine", "python", "modules", "module_paths", "conda_base",
        "system_site_packages", "device", "dtype", "lock",
    }
    if required - profile.keys() or profile.keys() - required - {"cpu_torch"}:
        raise ValueError(f"Invalid profile keys in {path}")
    name = profile["machine"]
    if name not in MACHINES or (machine and name != machine):
        raise ValueError(f"Profile machine {name!r} does not match {machine!r}")
    if profile["device"] != MACHINES[name]:
        raise ValueError("Profile device does not match the machine")
    for field in ("modules", "module_paths"):
        if not isinstance(profile[field], list) or not all(
            isinstance(item, str) and item and "\n" not in item for item in profile[field]
        ):
            raise ValueError(f"{field} must be a list of strings")
    for field in ("conda_base", "system_site_packages"):
        if not isinstance(profile[field], bool):
            raise ValueError(f"{field} must be boolean")
    for field in ("python", "lock"):
        if not isinstance(profile[field], str) or not profile[field]:
            raise ValueError(f"{field} must be a nonempty string")
    if name == "crux":
        if profile["system_site_packages"] or not profile.get("cpu_torch", "").endswith("+cpu"):
            raise ValueError("Crux requires an isolated environment and a pinned CPU torch")
    else:
        prefix = "conda/" if name == "polaris" else "frameworks/"
        if not profile["system_site_packages"] or not any(
            m.startswith(prefix) and m[len(prefix):] for m in profile["modules"]
        ):
            raise ValueError(f"{name} requires an overlay and an explicitly versioned {prefix} module")
        if profile["conda_base"] != (name == "polaris"):
            raise ValueError("Polaris must activate conda base; Aurora frameworks handles activation")
    if profile["dtype"] != ("float32" if name == "aurora" else "float64"):
        raise ValueError("Unexpected default dtype for this machine")
    profile["lock"] = str((path.parent / profile["lock"]).resolve())
    return profile


def profile_identity(profile):
    return {k: v for k, v in profile.items() if k != "lock"}


def module_setup(profile):
    lines = []
    if profile["modules"]:
        lines += [
            'if ! type module >/dev/null 2>&1; then',
            '    source /etc/profile || return',
            'fi',
            'type module >/dev/null 2>&1 || { echo "Environment Modules is unavailable" >&2; return 1; }',
        ]
        for path in profile["module_paths"]:
            lines.append(f"module use {shlex.quote(path)} || return")
        for module in profile["modules"]:
            lines.append(f"module load {shlex.quote(module)} || return")
    if profile["conda_base"]:
        lines += [
            'if ! declare -F conda >/dev/null; then',
            '    local conda_root',
            '    conda_root=$(conda info --base) || return',
            '    source "$conda_root/etc/profile.d/conda.sh" || return',
            'fi',
            'conda activate base || return',
        ]
    return "\n".join(lines)


def shell_command(profile, command):
    # Profile fields are quoted, never evaluated as arbitrary shell fragments.
    script = (
        'set -eo pipefail\n'
        'unset PYTHONPATH PYTHONHOME VIRTUAL_ENV\n'
        'export PYTHONNOUSERSITE=1\n'
        '_cg_modules() {\n' + (module_setup(profile) or ":") + '\n}\n'
        '_cg_modules\nexec "$@"\n'
    )
    return ["bash", "--noprofile", "--norc", "-c", script, "chemgraph-site", *map(str, command)]


def run(profile, command, *, log=None, capture=False, check=True):
    args = shell_command(profile, command)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "UV_"))}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INPUT="1", UV_NO_CONFIG="1",
               UV_PYTHON_DOWNLOADS="never")
    if log:
        log.write("$ " + shlex.join(list(map(str, command))) + "\n")
        log.flush()
    if capture:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        if log:
            log.write(result.stdout + result.stderr)
            log.flush()
    else:
        result = subprocess.run(args, text=True, stdout=log, stderr=log, env=env)
    if check and result.returncode:
        detail = result.stderr.strip() if capture else "See the installation log."
        raise RuntimeError(f"Command failed ({result.returncode}): {shlex.join(list(map(str, command)))}\n{detail}")
    return result


def snapshot(profile, python=None, log=None):
    result = run(profile, [python or profile["python"], LIB / "probe.py", "snapshot"],
                 log=log, capture=True)
    return json.loads(result.stdout)


def check_base(profile, base):
    if base["platform"] != "Linux" or base["architecture"] != "x86_64":
        raise ValueError("Prepare and install ALCF environments on Linux x86_64")
    if not (3, 11) <= tuple(base["python_version"][:2]) <= (3, 13):
        raise ValueError("The selected site Python must be 3.11–3.13")
    if base["is_venv"]:
        raise ValueError("Select a base Python, not an already active virtual environment")
    if profile["system_site_packages"] and "torch" not in base["packages"]:
        raise ValueError("The site module did not provide PyTorch")


def assert_base_matches(expected, actual):
    if expected != actual:
        differences = [k for k in expected if expected[k] != actual.get(k)]
        raise ValueError("Base environment changed; prepare a new lock. Changed: " + ", ".join(differences))


def pip_check(profile, python, log=None):
    result = run(profile, [python, "-m", "pip", "check"], log=log, capture=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    if result.returncode == 1 and not result.stdout.strip():
        raise RuntimeError("pip check failed without a dependency report: " + result.stderr)
    return sorted(result.stdout.splitlines()) if result.returncode else []
