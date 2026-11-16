# shellcheck shell=bash disable=SC2034
# ===========================================================================
# common.sh -- settings and helpers for the automated releases. Source it with
# the machine name:  source common.sh polaris
#
# Layout under ROOT (machines/<machine>.conf):
#   <machine>_chemgraph_env -> releases/<machine>/<release>   the promoted release
#   releases/<machine>/<release>/
#     venv/  activate.sh  requirements.lock  src/  build.log  verify.log  status.json
#     BUILT | VERIFIED | FAILED                               markers
#   auto/            these scripts and the locks, copied by update.sh
#   mirrors/         bare clones of ChemGraph and IQC (test sources)
#   uv/              uv's Python installs and cache
# A release is never modified after it is built; a new commit gets a new one.
# ===========================================================================
AUTO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MACHINE=$1
if [ -z "$MACHINE" ] || [ ! -f "$AUTO/machines/$MACHINE.conf" ]; then
  echo "unknown machine '$MACHINE' (see $AUTO/machines)" >&2
  return 1
fi
# shellcheck disable=SC1090
source "$AUTO/machines/$MACHINE.conf"

PYTHON_VERSION=3.11.14
ENV_NAME=${MACHINE}_chemgraph_env
CURRENT=$ROOT/$ENV_NAME
RELEASES=$ROOT/releases/$MACHINE
LOCKS=$AUTO/locks
MIRRORS=$ROOT/mirrors
KEEP=${KEEP:-3}                      # releases kept besides the promoted one
JOBNAME=chemgraph_$MACHINE
PBS_WALLTIME=${PBS_WALLTIME:-01:00:00}
UV=${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}
export UV_PYTHON_INSTALL_DIR=$ROOT/uv/python UV_CACHE_DIR=$ROOT/uv/cache
export UV_LINK_MODE=copy             # a release must not share inodes with the cache
export PATH=/opt/pbs/bin:$PATH

say() { echo "[$(date '+%F %T')] $MACHINE: $*"; }

proxy() {
  export http_proxy=http://proxy.alcf.anl.gov:3128 https_proxy=http://proxy.alcf.anl.gov:3128
  # Loopback bypasses the proxy: ChemGraph's tests start mock servers on 127.0.0.1.
  export no_proxy="127.0.0.1,localhost,::1,.alcf.anl.gov" NO_PROXY="127.0.0.1,localhost,::1,.alcf.anl.gov"
}

# The release the stable name points to ("" if none).
current_release() {
  [ -L "$CURRENT" ] && basename "$(readlink "$CURRENT")"
}

# lock_pin <lock> <package>: the version a lock pins, or the commit of a git requirement.
lock_pin() {
  sed -nE "s/^$2==([^+ ]+).*/\1/p; s/^$2 @ git\+[^@]+@([0-9a-f]+).*/\1/p" "$1" | head -1
}

# The shell lines a user sources to get the release at $1 (its real path, so
# a later promotion never changes a running job's environment).
activate_script() {
  cat <<EOS
# ChemGraph on ALCF ${MACHINE^}, release $(basename "$1"):
#   source $CURRENT/activate.sh
ulimit -c 0 2>/dev/null || true
export http_proxy=http://proxy.alcf.anl.gov:3128
export https_proxy=http://proxy.alcf.anl.gov:3128
export no_proxy="127.0.0.1,localhost,::1,.alcf.anl.gov"
export NO_PROXY="\$no_proxy"
export OMP_NUM_THREADS="\${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="\${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="\${MKL_NUM_THREADS:-1}"
# Keep packages in ~/.local (e.g. another torch) out of the shared env.
export PYTHONNOUSERSITE=1
source $1/venv/bin/activate
EOS
}

# submit_verify <release>: queue the compute-node verification of a built release.
submit_verify() {
  local rel=$RELEASES/$1 q out
  for q in $PBS_QUEUE ${PBS_QUEUE_FALLBACK:-}; do
    out=$(cd "$AUTO" && qsub -N "$JOBNAME" -A "$PBS_ACCOUNT" -q "$q" -l "select=$PBS_SELECT" \
      -l "walltime=$PBS_WALLTIME" -l "filesystems=$PBS_FILESYSTEMS" -j oe -o "$rel/verify.pbs.out" \
      -v "MACHINE=$MACHINE,RELEASE=$1" verify.pbs 2>&1) && { say "verify job $out ($q)"; return 0; }
    say "qsub to $q failed: $out"
  done
  return 1
}
