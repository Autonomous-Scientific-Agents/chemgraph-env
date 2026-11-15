#!/usr/bin/env python
"""check_lock.py <lock> <hardware-packages output>: run with a release's python.

Fails unless every package in the lock is installed at exactly its locked
version (torch: its locked version, any local build tag), and every installed
package that is not in the lock is a dependency of torch, i.e. part of this
machine's torch build. Writes those torch-build packages to the output file."""
import importlib.metadata as md
import re
import sys


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def locked(path):
    pins = {}
    for line in open(path):
        line = line.strip()
        m = re.match(r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(==|@)\s*(\S+)", line)
        if m and not line.startswith("#"):
            pins[norm(m.group(1))] = m.group(3) if m.group(2) == "==" else None
    return pins


def requires(name, extras=()):
    """(name, extras) of what an installed package requires, with the given extras."""
    out = set()
    for req in md.distribution(name).requires or []:
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(?:\[([^\]]*)\])?", req)
        wanted = re.search(r"""extra\s*==\s*['"]([^'"]+)['"]""", req)
        if m and (not wanted or norm(wanted.group(1)) in extras):
            out.add((norm(m.group(1)), tuple(sorted(norm(e) for e in (m.group(2) or "").split(",") if e.strip()))))
    return out


pins = locked(sys.argv[1])
installed = {norm(d.metadata["Name"]): d.version for d in md.distributions()}
bad = []
for name, version in sorted(pins.items()):
    have = installed.get(name)
    if have is None:
        bad.append(f"{name}: not installed")
    elif version is not None and have.split("+")[0] != version.split("+")[0]:
        bad.append(f"{name}: {have} installed, {version} locked")
    elif version is not None and name != "torch" and have != version:
        bad.append(f"{name}: {have} installed, {version} locked")

# Packages reachable from torch's requirements, extras included (cu126 torch
# requires cuda-toolkit[cublas,...]), are its build's own (CUDA, XPU).
torch_deps, seen, todo = set(), set(), [("torch", ())]
while todo:
    for dep in requires(*todo.pop()) - seen:
        seen.add(dep)
        if dep[0] in installed:
            torch_deps.add(dep[0])
            todo.append(dep)
extra = sorted(set(installed) - set(pins))
foreign = [n for n in extra if n not in torch_deps]
if foreign:
    bad.append("installed but neither locked nor part of torch: " + ", ".join(foreign))
with open(sys.argv[2], "w") as fh:
    fh.writelines(f"{n}=={installed[n]}\n" for n in extra)
if bad:
    print("lock check FAILED:\n  " + "\n  ".join(bad))
    sys.exit(1)
print(f"lock check OK: {len(pins)} locked packages at their versions; "
      f"{len(extra)} from the torch build")
