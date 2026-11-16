#!/bin/bash
# ===========================================================================
# verify.sh <machine> <release> -- runs in the verify job on a compute node.
#
# Checks the release in a fresh shell (verify.py: imports, the machine's
# device, tblite and PySCF energies), then runs the ChemGraph and IQC test
# suites from the locked sources. On success it marks the release VERIFIED
# and, if it is newer than the promoted one, points <machine>_chemgraph_env
# at it in one atomic rename; on failure it marks it FAILED and the promoted
# release stays as it is. Jobs that already sourced a release keep using it.
# ===========================================================================
set -o pipefail

main() {
  # shellcheck disable=SC1091
  source "$(dirname "${BASH_SOURCE[0]}")/common.sh" "$1" || return 1
  local id=$2 rel work cg cg_rc iqc iqc_rc result message
  rel=$RELEASES/$id
  [ -f "$rel/BUILT" ] || { say "$id is not built"; return 1; }
  exec >>"$rel/verify.log" 2>&1
  say "verifying $id on $(hostname)"
  export OMP_NUM_THREADS=$THREADS OPENBLAS_NUM_THREADS=$THREADS MKL_NUM_THREADS=$THREADS

  # Tests write into their directory: run them on a node-local copy.
  work=$(mktemp -d "${TMPDIR:-/tmp}/chemgraph-verify-XXXXXX")
  cp -r "$rel/src/." "$work/"
  cg="not run"; iqc="not run"; cg_rc=1; iqc_rc=1
  if bash -c "source '$rel/activate.sh' && DEVICE=$DEVICE python '$AUTO/verify.py'"; then
    # shellcheck disable=SC2086
    cg=$(run_pytest "$rel" "$work/ChemGraph" 2400 $PYTEST_ARGS); cg_rc=$?
    iqc=$(run_pytest "$rel" "$work/IQC" 1200); iqc_rc=$?
    say "ChemGraph pytest: $cg (exit $cg_rc)"
    say "IQC pytest: $iqc (exit $iqc_rc)"
    message="ChemGraph and IQC tests"
  else
    message="core checks failed (imports, $DEVICE, calculators)"
  fi
  rm -rf "$work"

  if [ "$cg_rc" = 0 ] && [ "$iqc_rc" = 0 ]; then
    result=verified
    rm -f "$rel/FAILED"
    touch "$rel/VERIFIED"
    promote "$id"
  else
    result=failed
    echo "verify: $message" > "$rel/FAILED"
    say "VERIFY FAILED: $message; $ENV_NAME stays at $(current_release)"
  fi
  write_status "$rel" "$result" "$message" "$cg" "$iqc"
  chmod -R a+rX "$rel" 2>/dev/null
  bash "$AUTO/status.sh" "$MACHINE"
}

# run_pytest <release> <dir> <seconds> [args]: summary line; pytest's exit status.
# The full output goes to <release>/pytest-<repo>.txt.
run_pytest() {
  local rel=$1 dir=$2 limit=$3 out rc
  shift 3
  out=$rel/pytest-$(basename "$dir").txt
  (cd "$dir" && timeout "$limit" bash -c "source '$rel/activate.sh' && python -m pytest -q -rfE -p no:cacheprovider \"\$@\"" _ "$@") > "$out" 2>&1
  rc=$?
  grep -E 'passed|failed|error|no tests ran' "$out" | tail -1 | tr -s '=' | sed 's/^= *//; s/ *=$//'
  [ $rc -eq 124 ] && echo "timed out after ${limit}s"
  return $rc
}

# Point the stable name at a verified release unless a newer one is promoted,
# then delete all but the KEEP newest other releases.
promote() {
  local id=$1 cur old
  cur=$(current_release)
  if [ -n "$cur" ] && [[ ! "$id" > "$cur" ]]; then
    say "verified $id; $ENV_NAME stays at $cur"
    return
  fi
  ln -sfn "releases/$MACHINE/$id" "$CURRENT.new" && mv -T "$CURRENT.new" "$CURRENT"
  say "PROMOTED $id: $CURRENT -> $(readlink "$CURRENT")"
  for old in $(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | grep -vxF "$id" | sort -r | tail -n +$((KEEP + 1))); do
    say "removing old release $old"
    rm -rf "${RELEASES:?}/$old"
  done
}

# write_status <release> <result> <message> <chemgraph pytest> <iqc pytest>
write_status() {
  python3 - "$@" "$(hostname)" "$MACHINE" > "$1/status.json" <<'PY'
import json, sys, time
rel, result, message, cg, iqc, host, machine = sys.argv[1:8]
print(json.dumps({"release": rel.rstrip("/").rsplit("/", 1)[-1], "machine": machine,
                  "result": result, "message": message, "host": host,
                  "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                  "chemgraph_pytest": cg, "iqc_pytest": iqc}, indent=2))
PY
}

main "$@"
