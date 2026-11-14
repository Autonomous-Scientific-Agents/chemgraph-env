"""Run with the target interpreter to inspect its effective package environment."""

import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import sys


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def distributions():
    # sys.path order matters: an overlay may shadow a site distribution.
    found = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            found.setdefault(canonical(name), dist)
    return found


def snapshot():
    return {
        "platform": platform.system(),
        "architecture": platform.machine(),
        "python_version": list(sys.version_info[:3]),
        "executable": str(Path(sys.executable).resolve()),
        "prefix": str(Path(sys.prefix).resolve()),
        "is_venv": sys.prefix != sys.base_prefix,
        "loaded_modules": os.environ.get("LOADEDMODULES", ""),
        "packages": {
            name: {"version": dist.version, "location": str(Path(dist.locate_file("")).resolve())}
            for name, dist in sorted(distributions().items())
        },
    }


def audit():
    # pip is pinned in every overlay; use its vendored parser without adding
    # another bootstrap dependency to a site environment.
    from pip._vendor.packaging.requirements import Requirement

    found = distributions()
    active = {}
    pending = [("chemgraph", {"calculators"}), ("mace-torch", {"polar"}),
               ("graph-longrange", set()), ("tblite", set())]
    errors = set()
    while pending:
        name, extras = pending.pop()
        name = canonical(name)
        if name in active and extras <= active[name]:
            continue
        active.setdefault(name, set()).update(extras)
        dist = found.get(name)
        if dist is None:
            errors.add(f"Missing package: {name}")
            continue
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker and not any(req.marker.evaluate({"extra": extra})
                                      for extra in active[name] | {""}):
                continue
            dependency = canonical(req.name)
            installed = found.get(dependency)
            if installed is None:
                errors.add(f"{name} requires {req}; it is missing")
            elif req.specifier and not req.specifier.contains(installed.version, prereleases=True):
                errors.add(f"{name} requires {req}; found {installed.version}")
            pending.append((dependency, set(req.extras)))
    return {"checked": sorted(active), "errors": sorted(errors)}


if __name__ == "__main__":
    operation = sys.argv[1]
    result = snapshot() if operation == "snapshot" else audit()
    print(json.dumps(result, sort_keys=True))
    if result.get("errors"):
        raise SystemExit(1)
