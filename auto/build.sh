#!/bin/bash
# ===========================================================================
# build.sh <machine> <release> -- build a release from its lock on a login
# node (network through the proxy, no walltime limit, no allocation charged),
# then queue its verify job. Started by trigger.sh; safe to run by hand.
#
#   1. a uv venv with Python $PYTHON_VERSION
#   2. ChemGraph and IQC sources at the locked commits
#   3. torch, at the lock's version, built for this machine (TORCH_INDEX)
#   4. ChemGraph and IQC editable from those sources, and everything else exactly as locked, without resolving again (--no-deps)
#   5. `uv pip check`, and check_lock.py: every locked package at its exact
#      version, and nothing else but torch's own build (CUDA or XPU libraries)
# ===========================================================================
set -o pipefail

main() {
  # shellcheck disable=SC1091
  source "$(dirname "${BASH_SOURCE[0]}")/common.sh" "$1" || return 1
  local id=$2 rel torch repo url sha
  rel=$RELEASES/$id
  [ -f "$LOCKS/$id.lock" ] || { say "no lock for $id"; return 1; }
  mkdir -p "$RELEASES"
  exec 9>"$RELEASES/.build.lock"
  flock -n 9 || { say "another build is running"; return 0; }
  [ "$(current_release)" != "$id" ] || { say "$id is the promoted release; not rebuilding it"; return 1; }

  # A release is either complete or rebuilt from scratch.
  rm -rf "$rel"
  mkdir -p "$rel"
  exec >>"$rel/build.log" 2>&1
  umask 022
  proxy
  export PYTHONNOUSERSITE=1
  fail() { say "BUILD FAILED: $*"; echo "build: $*" > "$rel/FAILED"; bash "$AUTO/status.sh" "$MACHINE"; exit 1; }
  say "building $id on $(hostname) with $("$UV" --version)"
  cp "$LOCKS/$id.lock" "$rel/requirements.lock"
  torch=$(lock_pin "$rel/requirements.lock" torch)
  [ -n "$torch" ] || fail "no torch in the lock"

  "$UV" python install "$PYTHON_VERSION" || fail "uv python install"
  "$UV" venv --python "$PYTHON_VERSION" "$rel/venv" || fail "uv venv"
  pip() { "$UV" pip "$1" --python "$rel/venv/bin/python" "${@:2}"; }

  for repo in ChemGraph IQC; do
    case $repo in
      ChemGraph) url=https://github.com/argonne-lcf/ChemGraph.git; sha=$(lock_pin "$rel/requirements.lock" chemgraph) ;;
      IQC) url=https://github.com/Autonomous-Scientific-Agents/IQC.git; sha=$(lock_pin "$rel/requirements.lock" iqc) ;;
    esac
    mkdir -p "$MIRRORS" "$rel/src/$repo"
    (
      flock 8
      [ -d "$MIRRORS/$repo.git" ] || git clone -q --bare "$url" "$MIRRORS/$repo.git"
      git --git-dir "$MIRRORS/$repo.git" fetch -q origin '+refs/heads/*:refs/heads/*'
    ) 8>"$MIRRORS/.$repo.lock" || fail "fetch $repo"
    git --git-dir "$MIRRORS/$repo.git" archive "$sha" | tar -x -C "$rel/src/$repo" || fail "$repo sources at $sha"
  done

  say "torch==$torch from $TORCH_INDEX"
  pip install "torch==$torch" --index-url "$TORCH_INDEX" || fail "torch"
  # ChemGraph and IQC editable from those sources (their tests expect a source
  # checkout), everything else as locked.
  sed -e '/^torch==/d' -e "s|^chemgraph @ .*|-e $rel/src/ChemGraph|" -e "s|^iqc @ .*|-e $rel/src/IQC|" \
    "$rel/requirements.lock" > "$rel/requirements.install.txt"
  pip install --no-deps -r "$rel/requirements.install.txt" || fail "locked packages"
  pip check || fail "uv pip check: the $DEVICE torch build disagrees with the lock"
  "$rel/venv/bin/python" "$AUTO/check_lock.py" "$rel/requirements.lock" "$rel/torch-build-packages.txt" \
    || fail "installed versions differ from the lock"

  activate_script "$rel" > "$rel/activate.sh"
  chmod -R a+rX "$rel" "$UV_PYTHON_INSTALL_DIR" "$MIRRORS"
  touch "$rel/BUILT"
  say "built $id"
  submit_verify "$id" || fail "cannot queue the verify job"
  bash "$AUTO/status.sh" "$MACHINE"
}

main "$@"
