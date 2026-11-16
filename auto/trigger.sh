#!/bin/bash
# ===========================================================================
# trigger.sh <machine> <release> -- login-node entry point, run by update.sh.
#
# Cheap and idempotent: does nothing when <release> is already promoted, is
# being built or verified, or has failed. Otherwise it starts build.sh in the
# background (which queues the verify job), or queues the verify job of a
# built release. FORCE=1 retries a failed release, or re-tests the promoted one.
# ===========================================================================
set -o pipefail

main() {
  # shellcheck disable=SC1091
  source "$(dirname "${BASH_SOURCE[0]}")/common.sh" "$1" || return 1
  local id=$2 rel
  if [ -z "$id" ] || [ ! -f "$LOCKS/$id.lock" ]; then say "no lock for release '$id'"; return 1; fi
  rel=$RELEASES/$id
  mkdir -p "$RELEASES"
  date '+%F %T %Z' > "$RELEASES/last_check"

  if [ -n "$(qselect -u "$USER" -N "$JOBNAME" 2>/dev/null)" ]; then
    say "verify job already queued or running"
  elif ! flock -n "$RELEASES/.build.lock" true; then
    say "build in progress"
  elif [ "$(current_release)" = "$id" ] && [ "${FORCE:-0}" != 1 ]; then
    say "up to date ($id)"
  elif [ -f "$rel/FAILED" ] && [ "${FORCE:-0}" != 1 ]; then
    say "$id failed ($(head -1 "$rel/FAILED")); waiting for a new commit (FORCE=1 to retry)"
  elif [ -f "$rel/BUILT" ]; then
    rm -f "$rel/FAILED"
    submit_verify "$id"
  else
    say "building $id"
    rm -f "$rel/FAILED"   # so the status says "building" until build.sh starts
    setsid nohup bash "$AUTO/build.sh" "$MACHINE" "$id" >/dev/null 2>&1 < /dev/null &
  fi
  bash "$AUTO/status.sh" "$MACHINE" >/dev/null 2>&1 || true
}

main "$@"
