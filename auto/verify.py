#!/usr/bin/env python
"""Core checks of a release, run by verify.sh in the activated env: imports, the
machine's torch device (DEVICE=cuda|xpu|cpu), and tblite and PySCF energies.
Exits non-zero on any failure. The test suites run separately."""
import importlib
import os
import sys


def fail(message):
    print(message)
    sys.exit(1)


bad = []
for name in ["numpy", "ase", "rdkit", "pymatgen", "mace", "tblite.ase", "pyscf", "chemgraph",
             "iqc", "mpi4py"]:
    try:
        importlib.import_module(name)
    except Exception as exc:
        bad.append(f"IMPORT FAIL {name} -> {exc!r:.120}")
if bad:
    fail("\n".join(bad))

import numpy  # noqa: E402
import torch  # noqa: E402

device = os.environ.get("DEVICE", "cpu")
print("numpy", numpy.__version__, "| torch", torch.__version__, "| device", device)
if device == "xpu":
    if not (getattr(torch, "xpu", None) and torch.xpu.is_available()):
        fail("XPU NOT AVAILABLE")
    print("xpu devices:", torch.xpu.device_count())
elif device == "cuda":
    if not torch.cuda.is_available():
        fail("CUDA NOT AVAILABLE")
    print("cuda devices:", torch.cuda.device_count(), torch.cuda.get_device_name(0))
torch.ones(8, device=device).sum().item()

from ase.build import molecule  # noqa: E402
from pyscf import gto, scf  # noqa: E402
from tblite.ase import TBLite  # noqa: E402

water = molecule("H2O")
water.calc = TBLite(method="GFN2-xTB", verbosity=0)
energy = water.get_potential_energy()
print("tblite GFN2-xTB H2O (eV):", round(energy, 4))
if not -139.0 < energy < -137.0:
    fail("tblite energy out of range")

mol = gto.M(atom="O 0 0 0; H 0 0 0.958; H 0 0.926 -0.24", basis="sto-3g", verbose=0)
ehf = scf.RHF(mol).kernel()
print("pyscf RHF/STO-3G H2O (Ha):", round(ehf, 4))
if not -75.1 < ehf < -74.8:
    fail("pyscf energy out of range")
print("CORE VERIFY OK")
