#!/usr/bin/env python3
"""prepare-lock entry point; see --help."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from cli import main

if __name__ == "__main__":
    raise SystemExit(main(["prepare-lock", *sys.argv[1:]]))
