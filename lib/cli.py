#!/usr/bin/env python3
"""Maintainer command-line interface."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

if sys.version_info < (3, 11):
    raise SystemExit("The toolkit needs Python >=3.11. Load site Python first or set CHEMGRAPH_BOOTSTRAP_PYTHON.")

from core import ROOT, absolute, load_profile
from handoff import prepare_iri, publish, validate
from install import install, prepare_lock
from models import catalog, provision, select_models


def parser():
    cli = argparse.ArgumentParser(description="Build and validate shared ChemGraph environments.")
    commands = cli.add_subparsers(dest="command", required=True)
    build = commands.add_parser("install", help="Install into a new, final versioned prefix")
    build.add_argument("machine", choices=["crux", "polaris", "aurora"])
    build.add_argument("method", choices=["uv", "native"])
    build.add_argument("--profile", type=Path)
    build.add_argument("--lock", type=Path, help="Override the profile's lock directory")
    build.add_argument("--prefix", type=absolute, required=True)
    build.add_argument("--model-root", type=absolute, required=True)
    build.add_argument("--dry-run", action="store_true")

    lock = commands.add_parser("prepare-lock", help="Resolve and check an overlay on its target machine")
    lock.add_argument("--profile", type=Path, required=True)
    lock.add_argument("--output", type=absolute, required=True)
    lock.add_argument("--dry-run", action="store_true")

    models = commands.add_parser("provision-models", help="Download and verify shared weights")
    models.add_argument("--model-root", type=absolute, required=True)
    models.add_argument("--source", type=absolute, help="Use a local checkpoint instead of downloading")
    models.add_argument("--model", help="Provision only this catalog model; required with --source for multiple models")
    models.add_argument("--dry-run", action="store_true")

    verify = commands.add_parser("validate", help="Run checks under the installed setup.sh")
    verify.add_argument("kind", choices=["compute", "access"])
    verify.add_argument("--prefix", type=absolute, required=True)
    verify.add_argument("--output", type=absolute, required=True)

    iri = commands.add_parser("prepare-iri", help="Prepare, but do not submit, an IRI acceptance run")
    iri.add_argument("--prefix", type=absolute, required=True)
    iri.add_argument("--output", type=absolute, required=True)
    iri.add_argument("--calculator", choices=["mace_mp", "mace_polar"], default="mace_polar",
                     help="Acceptance calculator; publishing requires the default mace_polar run")

    release = commands.add_parser("publish", help="Emit skill snippets after acceptance")
    release.add_argument("--prefix", type=absolute, required=True)
    release.add_argument("--compute-report", type=absolute, required=True)
    release.add_argument("--access-report", type=absolute, required=True)
    release.add_argument("--iri-run", type=absolute, required=True)
    release.add_argument("--iri-job-id", required=True)
    release.add_argument("--output", type=absolute, required=True)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"install", "prepare-lock"}:
            path = args.profile or ROOT / "profiles" / f"{args.machine}.toml"
            profile = load_profile(path, getattr(args, "machine", None))
            if getattr(args, "lock", None):
                profile["lock"] = str(args.lock.resolve())
            if args.dry_run:
                print(json.dumps({
                    "action": args.command, "profile": profile,
                    "method": getattr(args, "method", "native reference"),
                    "destination": str(getattr(args, "prefix", None) or args.output),
                    "model_root": str(getattr(args, "model_root", "")),
                    "prepared_lock_present": (Path(profile["lock"]) / "lock.json").is_file(),
                    "note": "No modules loaded, packages installed, files written, or network accessed.",
                }, indent=2))
                return 0
            if args.command == "prepare-lock":
                prepare_lock(profile, args.output)
            else:
                install(profile, args.method, args.prefix, args.model_root,
                        catalog(ROOT / "models/catalog.toml"))
        elif args.command == "provision-models":
            data = select_models(catalog(ROOT / "models/catalog.toml"), args.model)
            if args.dry_run:
                print(json.dumps({"root": str(args.model_root), "catalog": data}, indent=2))
            else:
                for record in provision(args.model_root, data, args.source):
                    print(record["path"])
        elif args.command == "validate":
            validate(args.prefix, args.kind, args.output)
        elif args.command == "prepare-iri":
            prepare_iri(args.prefix, args.output, args.calculator)
        else:
            publish(args.prefix, args.compute_report, args.access_report,
                    args.iri_run, args.iri_job_id, args.output)
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
