#!/bin/bash
# ===========================================================================
# update.sh [machine ...] -- the cron entry point; run it off-site, since ALCF
# login nodes have no user cron. For each machine (default: all in machines/):
#   1. lock.sh: the release id for the current ChemGraph and IQC commits
#      (a new shared lock only when one of them changed)
#   2. copy these scripts and the lock to <ROOT>/auto over SSH
#   3. trigger.sh on the login node, which builds and verifies the release
#      if it is new, and otherwise just refreshes the status
# SSH uses BatchMode and never prompts for MFA: keep a ControlMaster open per
# machine (`ssh -fN <machine>`), with the machine name as the SSH host alias.
# FORCE=1 retries a failed release or re-tests the promoted one.
#
#   17 * * * * /path/to/chemgraph-env/auto/update.sh >> $HOME/chemgraph-env-update.log 2>&1
# ===========================================================================
set -o pipefail

main() {
  local here id m root machines=("$@")
  here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  [ ${#machines[@]} -gt 0 ] || mapfile -t machines < <(find "$here/machines" -name '*.conf' -printf '%f\n' | sed 's/\.conf$//' | sort)
  id=$(bash "$here/lock.sh") || { echo "[$(date '+%F %T')] update: no lock" >&2; return 1; }
  for m in "${machines[@]}"; do
    root=$(bash -c 'source "$1" && echo "$ROOT"' _ "$here/machines/$m.conf") || continue
    if ! tar -cf - -C "$here" common.sh trigger.sh build.sh verify.sh verify.pbs verify.py check_lock.py status.sh machines \
           -C "${CHEMGRAPH_ENV_STATE:-$HOME/.chemgraph-env}" "locks/$id.lock" \
         | ssh -o BatchMode=yes "$m" "mkdir -p '$root/auto' && tar -xf - -C '$root/auto'"; then
      echo "[$(date '+%F %T')] $m: ssh failed (open a connection: ssh -fN $m)"
      continue
    fi
    ssh -n -o BatchMode=yes "$m" "FORCE=${FORCE:-0} bash '$root/auto/trigger.sh' '$m' '$id'"
  done
}

main "$@"
