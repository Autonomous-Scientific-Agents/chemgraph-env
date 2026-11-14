"""Provision immutable model files outside virtual environments."""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import tempfile
import tomllib
import urllib.request

from core import digest
from calculators import DEFAULT_MODELS


def catalog(path):
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(data.get("mace_version", ""))) or not data.get("models"):
        raise ValueError("Catalog must declare an exact MACE release version and models")
    names, files = set(), set()
    for model in data["models"]:
        if model.get("calculator_type") not in DEFAULT_MODELS:
            raise ValueError("Model calculator_type must be mace_mp or mace_polar")
        if not re.fullmatch(r"[a-z0-9-]+", model["name"]):
            raise ValueError("Invalid model name")
        if not re.fullmatch(r"[0-9a-f]{64}", model["sha256"]):
            raise ValueError("Model requires a SHA-256 checksum")
        filename = model["cache_filename"]
        if not re.fullmatch(r"[A-Za-z0-9_]+", filename):
            raise ValueError("Invalid MACE cache filename")
        if model["name"] in names or filename in files:
            raise ValueError("Duplicate model name or cache filename")
        names.add(model["name"])
        files.add(filename)
        if not model["url"].startswith("https://") or not model["mace_url"].startswith("https://"):
            raise ValueError("Model source must use HTTPS")
    supported = {(model["calculator_type"], model["name"]) for model in data["models"]}
    if not set(DEFAULT_MODELS.items()) <= supported:
        raise ValueError("Catalog must include the default MACE-MP and MACE-Polar checkpoints")
    return data


def check_mace_pin(data, requirements):
    pins = re.findall(r"^mace-torch\[([^\]]+)\]==([0-9]+\.[0-9]+\.[0-9]+)\s*$",
                      requirements, re.MULTILINE)
    if len(pins) != 1 or "polar" not in pins[0][0].split(",") or pins[0][1] != data["mace_version"]:
        raise ValueError("Pin mace-torch[polar] to the catalog's exact mace_version and regenerate locks")


def select_models(data, name=None):
    if name is None:
        return data
    selected = [model for model in data["models"] if model["name"] == name]
    if not selected:
        raise ValueError(f"Model is not in the maintained catalog: {name}")
    return {**data, "models": selected}


def model_path(root, model):
    return Path(root) / "mace" / model["name"] / model["sha256"] / "model.model"


@contextmanager
def directory_lock(path):
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError(f"Another operation holds {path}; inspect it before removing a stale lock") from None
    try:
        yield
    finally:
        path.rmdir()


def provision(root, data, source=None):
    if source and len(data["models"]) != 1:
        raise ValueError("Use --model NAME with --source when the catalog contains multiple models")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with directory_lock(root / ".provision.lock"):
        for model in data["models"]:
            destination = model_path(root, model)
            if os.path.lexists(destination):
                if destination.is_symlink() or digest(destination) != model["sha256"]:
                    raise ValueError(f"Existing model is not the expected immutable file: {destination}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    if source:
                        input_stream = open(source, "rb")
                    else:
                        input_stream = urllib.request.urlopen(model["url"], timeout=120)
                    with input_stream as incoming:
                        while block := incoming.read(1024 * 1024):
                            output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if digest(temporary) != model["sha256"]:
                    raise ValueError(f"Checksum mismatch for {model['name']}; model was not published")
                os.chmod(temporary, 0o444)
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return records(root, data)


def records(root, data):
    result = []
    for model in data["models"]:
        path = model_path(root, model)
        if not path.is_file() or digest(path) != model["sha256"]:
            raise ValueError(f"Provision the missing or invalid model first: {path}")
        result.append({**model, "path": str(path.resolve())})
    return result
