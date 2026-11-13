#!/usr/bin/env python3
"""Lint entry points and generated activation scripts without installing anything."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from core import MACHINES, ROOT, load_profile
from install import setup_script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shellcheck", default="shellcheck")
    args = parser.parse_args()
    entries = sorted((ROOT / "machines").glob("*/*.sh"))
    for path in entries:
        subprocess.run(["bash", "-n", str(path)], check=True)
    subprocess.run([args.shellcheck, *map(str, entries)], check=True)
    with tempfile.TemporaryDirectory(prefix="chemgraph-shell-") as directory:
        work = Path(directory)
        for machine in MACHINES:
            generated = work / f"{machine}.sh"
            generated.write_text(setup_script(work / "example deployment",
                                             load_profile(ROOT / "profiles" / f"{machine}.toml")))
            subprocess.run(["bash", "-n", str(generated)], check=True)
            # The target venv and site /etc/profile are unavailable in CI.
            subprocess.run([args.shellcheck, "-e", "SC1091", str(generated)], check=True)


if __name__ == "__main__":
    main()
