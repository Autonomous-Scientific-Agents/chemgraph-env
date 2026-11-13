#!/bin/bash
set -eo pipefail
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export PYTHONNOUSERSITE=1
exec "${CHEMGRAPH_BOOTSTRAP_PYTHON:-python3}" "$REPO_ROOT/lib/cli.py" install aurora uv "$@"
