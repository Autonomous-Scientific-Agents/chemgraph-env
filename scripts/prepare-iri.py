#!/usr/bin/env python3
"""prepare-iri entry point; see --help."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from cli import main

if __name__ == "__main__":
    raise SystemExit(main(["prepare-iri", *sys.argv[1:]]))
