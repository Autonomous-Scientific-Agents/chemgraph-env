#!/bin/bash
# ===========================================================================
# lock.sh -- resolve the dependency lock shared by every machine and print its
# release id, <YYYYmmddTHHMM>-cg<ChemGraph commit>-iqc<IQC commit>-in<inputs hash>.
#
# Runs where update.sh runs (any Linux x86_64 host with git and uv). A lock is
# made only when ChemGraph or IQC has a new commit on main, or requirements.in
# changed; otherwise the existing lock is reused, so dependency versions change
# only together with a release. Locks are kept in $CHEMGRAPH_ENV_STATE/locks
# (default ~/.chemgraph-env/locks).
# ===========================================================================
set -o pipefail

main() {
  local here state cg iqc inputs key id existing tmp
  here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  state=${CHEMGRAPH_ENV_STATE:-$HOME/.chemgraph-env}
  mkdir -p "$state/locks"
  cg=$(git ls-remote https://github.com/argonne-lcf/ChemGraph.git refs/heads/main | cut -f1)
  iqc=$(git ls-remote https://github.com/Autonomous-Scientific-Agents/IQC.git refs/heads/main | cut -f1)
  if [ -z "$cg" ] || [ -z "$iqc" ]; then echo "lock: cannot resolve ChemGraph/IQC main" >&2; return 1; fi

  inputs=$(sha256sum "$here/requirements.in" | cut -c1-6)
  key=cg${cg:0:9}-iqc${iqc:0:9}-in$inputs
  existing=$(find "$state/locks" -name "*-$key.lock" | sort | tail -1)
  if [ -n "$existing" ]; then basename "$existing" .lock; return 0; fi

  id=$(date +%Y%m%dT%H%M)-$key
  tmp=$(mktemp -d "$state/.lock-XXXXXX")
  sed -e "s|ChemGraph.git@main|ChemGraph.git@$cg|" -e "s|IQC.git@main|IQC.git@$iqc|" \
    "$here/requirements.in" > "$tmp/requirements.in"
  # torch's CPU build has no GPU dependencies, so this resolves exactly the
  # packages every machine shares. Each machine adds its own torch build.
  if ! "${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}" pip compile "$tmp/requirements.in" -o "$tmp/requirements.lock" -q --no-header \
      --python-version 3.11 --python-platform x86_64-manylinux_2_28 \
      --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match; then
    rm -rf "$tmp"; echo "lock: resolution failed" >&2; return 1
  fi
  mv "$tmp/requirements.lock" "$state/locks/$id.lock"
  rm -rf "$tmp"
  echo "$id"
}

main "$@"
