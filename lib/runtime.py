"""Copied into each deployment. Only stdlib is needed to prepare a user's cache."""

import json
import os
from pathlib import Path
import sys


def prepare_cache(manifest):
    # setup.sh preserves this root across repeated activation; include uid even
    # with a shared override so users never write into each other's caches.
    root = Path(os.environ.get("CHEMGRAPH_CACHE_ROOT", str(Path.home() / ".cache/chemgraph")))
    if not root.is_absolute():
        raise ValueError("CHEMGRAPH_CACHE_ROOT must be absolute")
    cache = root / str(os.getuid()) / manifest["deployment_id"]
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    if cache.stat().st_uid != os.getuid():
        raise ValueError(f"Cache is owned by another user: {cache}")
    os.chmod(cache, 0o700)
    mace = cache / "mace"
    mace.mkdir(exist_ok=True, mode=0o700)
    for model in manifest["models"]:
        target = Path(model["path"])
        # Opening also checks ancestor traversal and read permission as this user.
        with target.open("rb") as handle:
            if not handle.read(1):
                raise ValueError(f"Empty shared checkpoint: {target}")
        link = mace / model["cache_filename"]
        try:
            link.symlink_to(target)
        except FileExistsError:
            if not link.is_symlink() or link.resolve() != target.resolve():
                raise ValueError(f"Cache entry conflicts with the deployment; preserved: {link}") from None
    return cache


if __name__ == "__main__":
    try:
        manifest = json.loads(Path(sys.argv[1]).read_text())
        print(prepare_cache(manifest))
    except (OSError, ValueError, KeyError) as error:
        print(f"ChemGraph cache setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
