#!/bin/bash
# ===========================================================================
# status.sh <machine> -- describe the promoted release, and the newest release
# if it is still building or failed, as JSON for the ALCF Dashboard
# (https://github.com/keceli/ALCF-Dashboard). Writes
#   releases/<machine>/status.json                      (world-readable)
#   ~/.alcf-dashboard/envs/chemgraph-<machine>.json     (read by the dashboard)
# Polaris, Sophia and Crux share /home, so the file name carries the machine.
# Reads package metadata only: no GPU needed, torch is not imported.
# ===========================================================================
set -o pipefail

main() {
  # shellcheck disable=SC1091
  source "$(dirname "${BASH_SOURCE[0]}")/common.sh" "$1" || return 1
  local cur latest state versions="{}" out
  cur=$(current_release)
  latest=$(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort | tail -1)
  if [ -z "$latest" ]; then state=none
  elif [ -f "$RELEASES/$latest/FAILED" ]; then state=failed
  elif [ -f "$RELEASES/$latest/VERIFIED" ]; then state=verified
  elif [ -f "$RELEASES/$latest/BUILT" ]; then state=verifying
  else state=building
  fi
  if [ -n "$cur" ]; then
    versions=$("$CURRENT/venv/bin/python" - <<'PY'
import json, sys
import importlib.metadata as md
out = {"python": "%d.%d.%d" % sys.version_info[:3]}
for p in ["torch", "numpy", "chemgraph", "iqc", "mace-torch", "tblite", "pyscf", "ase",
          "rdkit", "pymatgen", "parsl", "ensemble-launcher", "mpi4py"]:
    try:
        out[p] = md.version(p)
    except md.PackageNotFoundError:
        pass
print(json.dumps(out))
PY
) || versions="{}"
  fi
  out=$HOME/.alcf-dashboard/envs/chemgraph-$MACHINE.json
  mkdir -p "$(dirname "$out")" "$RELEASES"
  python3 - "$MACHINE" "$ENV_NAME" "$CURRENT" "$RELEASES" "$DEVICE" "${cur:-}" "${latest:-}" "$state" "$versions" \
    > "$RELEASES/status.json.tmp" <<'PY' || { rm -f "$RELEASES/status.json.tmp"; return 1; }
import json, os, sys
from datetime import datetime, timezone
machine, name, path, releases, device, cur, latest, state, versions = sys.argv[1:10]

def read(rel, fname):
    try:
        with open(os.path.join(releases, rel, fname)) as fh:
            return fh.read()
    except (OSError, TypeError):
        return None

def pins(rel):
    # The ChemGraph and IQC commits a release's lock names.
    found = {}
    for line in (read(rel, "requirements.lock") or "").splitlines():
        for pkg, key in (("chemgraph @ ", "ChemGraph"), ("iqc @ ", "IQC")):
            if line.startswith(pkg) and "@" in line[len(pkg):]:
                found[key] = line.rsplit("@", 1)[1][:9]
    return found

verified = json.loads(read(cur, "status.json") or "{}") if cur else {}
attempt = json.loads(read(latest, "status.json") or "{}") if latest else {}
if state == "verified" or (cur and latest == cur):
    result = "updated_ok"
elif state == "failed":
    result = "failed_kept_previous" if cur else "failed"
else:
    result = state
shown = attempt if attempt else verified
check = read("", "last_check")
au = {"result": result, "release": cur or None, "time": shown.get("time"),
      "chemgraph_pytest": shown.get("chemgraph_pytest"), "iqc_pytest": shown.get("iqc_pytest"),
      "last_check": check.strip() if check else None}
if latest and latest != cur:
    reason = (read(latest, "FAILED") or "").strip()
    au["pending"] = {"release": latest, "state": state, "reason": reason or None}
print(json.dumps({
    "name": name,
    "machine": machine,
    "path": path,
    "activate": "source %s/activate.sh" % path,
    "backend": {"xpu": "Intel XPU (PVC)", "cuda": "CUDA (NVIDIA A100)", "cpu": "CPU"}.get(device, device),
    "manager": "uv",
    "release": cur or None,
    "versions": json.loads(versions),
    "git": pins(cur) if cur else {},
    "auto_update": au,
    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
}, indent=2))
PY
  mv -f "$RELEASES/status.json.tmp" "$RELEASES/status.json"
  chmod a+r "$RELEASES/status.json"
  cp -f "$RELEASES/status.json" "$out"
}

main "$@"
