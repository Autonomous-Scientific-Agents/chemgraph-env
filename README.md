# ChemGraph environments for ALCF

Maintainer tools for building shared **ChemGraph + ASE + MACE-MP + MACE-Polar + tblite**
environments on Crux, Polaris, and Aurora. Users run ChemGraph elsewhere and
submit calculations through its IRI skill. They do not install an environment
or supply paths to the standard pretrained model.

**Status:** the toolkit has local tests. The supplied profiles are candidates,
not certified ALCF deployments. No machine lock or production environment path
is claimed to be validated. Prepare a lock and complete acceptance on each
machine before publishing a path.

| Machine | uv entry point | Native entry point | Base stack | Device |
| --- | --- | --- | --- | --- |
| Crux | `machines/crux/install-uv.sh` | `machines/crux/install-native.sh` | Selected site Python, isolated venv | CPU |
| Polaris | `machines/polaris/install-uv.sh` | `machines/polaris/install-native.sh` | `conda/2026-10-01`, activated base, venv overlay | CUDA |
| Aurora | `machines/aurora/install-uv.sh` | `machines/aurora/install-native.sh` | `frameworks/2026.1.0`, venv overlay | XPU |

Both methods on a machine use the **same site interpreter and dependency lock**.
The uv method uses `uv venv` and `uv pip install`; native uses `python -m venv`
and pip. Native Polaris extends the Conda base with a venv rather than cloning
or modifying the base. Aurora extends the frameworks environment similarly.
Crux installs CPU-only PyTorch into an isolated venv.

## Maintainer workflow

Run the following from a checkout on the **target machine**. Use an allocation
for substantial builds and all simulation tests. Use the documented site proxy
when downloads require it. No script acquires an allocation, authenticates to
IRI, or submits a job.

Prerequisites: Bash, Git, a bootstrap Python >=3.11, a selected site Python
3.11–3.13 with `venv`, and the required modules. The uv method also requires uv
on PATH. Scripts never install uv or download Python. If `python3` is too old,
load a suitable Python/module first; the shell installers also accept
`CHEMGRAPH_BOOTSTRAP_PYTHON=/absolute/path/to/python3.11`. For Python entry points,
invoke the selected bootstrap interpreter explicitly when needed.

### 1. Select a profile and storage

Review `profiles/<machine>.toml`. Set `python` to a base interpreter command or
absolute path, and use an explicit module version available on that machine.
Make another profile and lock when changing the Python/module combination.

Choose shared storage visible from compute nodes and accessible to intended
users. Polaris/Crux might use an Eagle project directory; Aurora might use a
Flare project directory. These are deployment choices, not hard-coded defaults.
All ancestor directories need traversal permission for intended users.

```bash
# Illustrative paths: replace PROJECT and select your release identifier.
DEPLOY_ROOT=/eagle/PROJECT/chemgraph
DEPLOY_PREFIX="$DEPLOY_ROOT/envs/polaris/uv/2026-11-13-r1"
umask 022
```

Use a maintainer-owned location. Ordinary users should have read/traverse access
to releases and model files, without write access. Keep permissions restricted
by the project directory or its ACL when the deployment is allocation-specific.
The scripts do not change ownership, ACLs, or permissions on existing parents.

### 2. Prepare and review a machine lock

```bash
python3 scripts/prepare-lock.py \
  --profile profiles/polaris.toml \
  --output "$PWD/requirements/locks/polaris"
```

This creates a temporary native reference environment, runs pip's resolver with
constraints protecting site accelerator/MPI packages, rejects any attempt to
shadow those packages, installs the exact resolved artifacts, and checks the
result. The temporary environment is removed; the lock directory must be new.

Review and commit `lock.json` and `overlay.txt`. The lock records the complete
base inventory, interpreter path/version, loaded modules, source requirements,
and the overlay checksum. `prepare.log` is a local diagnostic artifact.

The shared inputs pin ChemGraph to commit
`465e03436e56e1374480fe5a3bd4a735a1c56db2` and `mace-torch[polar]` to `0.3.17`.
The Polar engine, `graph-longrange`, is pinned to commit
`6a86de5e3ed35fd86a55fc046aa085fe48a72764` (v0.4.4) from
`WillBaldwin0/graph_electrostatics`, following the
[MACE-Polar installation guide](https://mace-docs.readthedocs.io/en/latest/guide/polar_mace.html).
Crux's initial profile pins CPU PyTorch `2.8.0+cpu`. These are deliberate version
choices; subsequent releases are upgraded explicitly, as described below.

**Why a separate lock step?** uv does not resolve against inherited site
packages. Both installers therefore consume the complete, already-resolved
overlay with `--no-deps`. Site packages are recorded and checked separately;
copying a generic `pip install chemgraph` into a uv overlay can shadow the
optimized GPU stack. Do not hand-edit the overlay to bypass a conflict.

### 3. Provision shared weights

```bash
python3 scripts/provision-models.py --model-root "$DEPLOY_ROOT/models"
```

This downloads `medium-mpa-0` and `polar-1-m`, verifies their pinned SHA-256
checksums, and publishes read-only files. Repeated runs verify and reuse them.
A bad or incomplete download is never published. Use `--model NAME` to provision
just one catalog entry. Both checkpoints must exist before installing an environment.

For offline provisioning, select the model associated with each local file:

```bash
python3 scripts/provision-models.py --model-root "$DEPLOY_ROOT/models" \
  --model medium-mpa-0 --source /absolute/path/to/mace-mpa-0-medium.model
python3 scripts/provision-models.py --model-root "$DEPLOY_ROOT/models" \
  --model polar-1-m --source /absolute/path/to/MACE-POLAR-1-M.model
```

The same checksum checks apply. MACE-MP weights use the MIT license; MACE-Polar
weights use [ASL](https://github.com/gabor1/ASL), as stated in their
[upstream release](https://github.com/ACEsuit/mace-foundations/releases/tag/mace_polar_1).
Model provenance and license identifiers are recorded in the deployment manifest.

Weights are outside Git and outside the virtual environments:

```text
<shared-root>/
  envs/<machine>/<method>/<release>/
    venv/
    setup.sh
    manifest.json
    install.log
    runtime.py
    validation-tools/
    overlay.txt
  models/mace/medium-mpa-0/<sha256>/model.model
  models/mace/polar-1-m/<sha256>/model.model
```

Provision once per filesystem accessible to the relevant machines. Different
module versions and both installer methods can share the same checkpoints.

### 4. Install into its final versioned directory

```bash
machines/polaris/install-uv.sh \
  --prefix "$DEPLOY_PREFIX" \
  --profile profiles/polaris.toml \
  --model-root "$DEPLOY_ROOT/models"
```

Choose `install-native.sh` for the native method and give it a different prefix.
Use the corresponding machine directory and profile for Crux or Aurora.
`--lock /absolute/lock-directory` overrides the profile's lock location.

`--dry-run` reads configuration and displays the intended deployment without
loading modules, installing packages, making network requests, or creating files.
It does not certify that the lock or site environment is usable.

Installation rejects an existing prefix and any base-stack drift. It copies
packages rather than linking them to uv's cache. Each deployment records its
toolkit source hashes and the unmodified installed ChemGraph `calculate.py`.
An interrupted or failed installation is marked failed and cannot be published.
Use a new prefix after correcting the cause; inspect old artifacts before
removing them manually. **Never move a completed venv to a different path.**

### 5. Validate the actual deployment

Inside a PBS compute allocation, run:

```bash
python3 scripts/validate.py compute \
  --prefix "$DEPLOY_PREFIX" --output "$PWD/acceptance-polaris-uv"
```

This loads `setup.sh` in a fresh noninteractive Bash process. It checks the
dependency closure and PyTorch origin, performs a device operation, and runs
EMT, tblite, and default/named MACE-MP and MACE-Polar energy/force calculations.
Polar checks also verify finite molecular dipoles and pass charge, multiplicity,
and external-field metadata through ChemGraph's calculator interface.
Model downloads/network connections are disabled during these calculations.
Default and named-model energies must agree within the smoke-test tolerance for
each model family. The requested machine device must work for both families;
there is no silent CPU fallback for an unsupported Aurora XPU operation.

From a **second authorized account**, run:

```bash
python3 scripts/validate.py access \
  --prefix /absolute/deployment --output /absolute/user-writable/access-check
```

This checks the shared interpreter, package/model access, private cache setup,
and that this account cannot write the deployment or checkpoints. Retain its
`report.json`. Cache fixtures in CI do not replace this
real second-account test.

Then prepare a fresh IRI acceptance directory:

```bash
python3 scripts/prepare-iri.py \
  --prefix "$DEPLOY_PREFIX" --output "$PWD/iri-acceptance-polaris-uv"
```

Stage and submit this directory through **ChemGraph's existing IRI skill** using
the correct project, queue, filesystems, and remote workspace. Use the generated
`launch.sh`: it invokes the unchanged `calculate.py` and records application
success afterward. The default acceptance job uses `mace_polar` with its model
omitted, exercising ChemGraph's `polar-1-m` default and the shared cache.
No model path appears in `input.json`. You can additionally prepare an MP job
with `--calculator mace_mp`; publishing requires the Polar IRI run, while the
compute report must cover both MP and Polar.
Collect the directory, including `acceptance.json`, `result.json`, and job logs.
Record the accepted IRI job ID and verify that this job completed successfully.

### 6. Emit the skill handoff

```bash
python3 scripts/publish.py \
  --prefix "$DEPLOY_PREFIX" \
  --compute-report "$PWD/acceptance-polaris-uv/report.json" \
  --access-report /absolute/collected/access-check/report.json \
  --iri-run /absolute/collected/iri-acceptance-polaris-uv \
  --iri-job-id FULL_ACCEPTED_IRI_JOB_ID \
  --output "$PWD/release-polaris-uv"
```

This writes `release.json`, `target-environment.toml`, and `calculators.json`.
It checks that all reports refer to this deployment and that IRI results match
their recorded checksum. The IRI transport and accepted IRI ID are **attested by
the maintainer**: this tool does not query the API, and an IRI ID need not equal
the PBS job ID. Local PBS results alone do not establish an IRI acceptance run.

Copy the two fields from `target-environment.toml` into the maintained ChemGraph
skill/deployment configuration. Do not replace a user's whole target file:
accounts, resource IDs, queues, and transfer settings remain deployment-specific.

```toml
environment_setup = "source /absolute/deployment/setup.sh"
python = "/absolute/deployment/venv/bin/python"
```

Use concrete versioned paths. Update a skill only after the replacement passes
acceptance; retain the old deployment while existing jobs may still use it.
Rollback selects the old path. This repository does not edit ChemGraph upstream.

## How models work for multiple users

`setup.sh` sets up a private, release-specific `$XDG_CACHE_HOME`, with symlinks
under `mace/` pointing to shared checkpoints. The default cache root is
`${XDG_CACHE_HOME:-$HOME/.cache}/chemgraph`, captured once per shell as
`CHEMGRAPH_CACHE_ROOT`; the cache contains UID and deployment-ID subdirectories.
Override `CHEMGRAPH_CACHE_ROOT` with an absolute user-writable location when
home storage is unsuitable. It must be visible wherever that user's job runs.

MACE sees the normal cache filename for each model family. ChemGraph can omit
`model`, use `model="medium-mpa-0"` with `calculator_type="mace_mp"`, or use
`model="polar-1-m"` with `calculator_type="mace_polar"`. No path lookup is added
to `run_ase_core()` and IRI needs no model-specific behavior.

**ChemGraph defaults to MACE-Polar when `graph-longrange` is installed.** Set
`calculator_type="mace_mp"` explicitly for MP calculations. The generated
`calculators.json` includes both families. Other models download into that user's writable
cache, subject to network access and their licenses. Explicit model paths still
work. Existing conflicting cache entries are preserved and reported as errors.

Runtime compilation caches and outputs stay user-writable. The setup script
does not set `CHEMGRAPH_LOG_DIR`; ChemGraph's job launcher sets it to the run
directory. It also does not select a calculator device: use the generated
examples (`cpu`, `cuda`, or `xpu`; `float32` on Aurora).

## Upgrading MACE

The implementation does not restrict deployments to a hard-coded MACE release.
Versions remain exact and reproducible for each deployment:

1. Update `mace-torch[polar]==...` in `requirements/base.in` and `mace_version`
   in `models/catalog.toml` together. A mismatch stops lock preparation/installation.
2. Check the new release's Polar requirements and update the pinned
   `graph-longrange` commit when needed. Keep the `polar` extra so the dependency
   audit checks its version requirements and transitive dependencies.
3. Verify the model URLs, loader/cache filenames, and default model names against
   that release; update the catalog and acceptance examples if they changed.
4. Regenerate each machine lock in a new directory, build a new versioned prefix,
   and rerun compute, second-account, and IRI acceptance. Reuse unchanged shared
   checkpoint files by checksum. An older lock is rejected after requirements change.

New version pins do not establish compatibility: the site PyTorch stack remains
protected, and the simulation tests must pass before publishing the new paths.

## Development and boundaries

```bash
python3 -m unittest discover -s tests -v
ruff check .
python3 scripts/check-shell.py
```

CI runs offline behavior tests on Python 3.11–3.13, Bash syntax, ShellCheck, and
Ruff. It covers installer routing, site-package protection, lock integrity,
model provisioning, cache concurrency, activation, and handoff validation.
CI never claims to validate ALCF GPU execution.

Revalidate after changing dependencies, models, or a site stack. Site updates
can retire a module underneath an otherwise immutable overlay. There is no
automatic upgrade, fallback module, force reinstall, or scheduler integration.
UMA, external engine builds, distributed tuning, and very-large-scale
Python import optimization are outside this first release.

References: [ChemGraph](https://github.com/argonne-lcf/ChemGraph),
[Crux Python](https://docs.alcf.anl.gov/crux/data-science/python/),
[Polaris Python](https://docs.alcf.anl.gov/polaris/data-science/python/),
[Aurora Python](https://docs.alcf.anl.gov/aurora/data-science/python/),
[uv site-package behavior](https://docs.astral.sh/uv/reference/cli/#uv-venv).
