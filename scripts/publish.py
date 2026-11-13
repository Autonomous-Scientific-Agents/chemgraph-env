#!/usr/bin/env python3
"""publish entry point; see --help."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from cli import main

if __name__ == "__main__":
    raise SystemExit(main(["publish", *sys.argv[1:]]))
