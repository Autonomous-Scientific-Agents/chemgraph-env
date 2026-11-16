# Automated releases

Scripts that keep a shared, tested ChemGraph environment named
`<machine>_chemgraph_env` on Aurora, Polaris, Sophia and Crux, and build a new
release whenever ChemGraph or IQC gets a new commit on `main`. They complement
the maintainer toolkit at the top of this repository: they follow its
principles (exact locks, immutable versioned prefixes, validation on a compute
node before a path is used) and automate them, so the environments track
`main` without manual work.

| Machine | Device | torch build | Verify job |
| --- | --- | --- | --- |
| Aurora | Intel GPU (PVC) | `+xpu` | `ChemGraph`, `debug` |
| Polaris | NVIDIA A100 | `+cu126` | `ChemGraph`, `debug` (else `debug-scaling`) |
| Sophia | NVIDIA A100 | `+cu126` | `ChemGraph`, `by-gpu` |
| Crux | CPU | `+cpu` | `Catalyst`, `debug` |

Machine settings are in [`machines/`](machines/).

## One version of everything

[`lock.sh`](lock.sh) resolves [`requirements.in`](requirements.in) once, with
ChemGraph and IQC at their current commits, into a lock that every machine
installs: the same Python (3.11.14), the same version of every package, and
the same torch version (2.13.0). Only torch's build differs per machine, so
that it can use the hardware. A machine installs that torch first, then the
locked packages with `--no-deps`, and `uv pip check` fails the build if the
torch build disagrees with the lock. The lock is resolved only when ChemGraph
or IQC changes, so other dependencies move only together with a release.

Everything uses [uv](https://docs.astral.sh/uv/) and its own Python, not a
site module: the result does not depend on a module that can be retired. The
cost is that the optimized site stacks (Aurora's frameworks module, a Cray
MPICH `mpi4py`) are not used. `mpi4py` comes with the `mpich` wheel, which
works on one node; rebuild it against the site MPI for multi-node runs.

## How a release is made

```text
update.sh  (off-site cron, hourly)
  lock.sh                 release id <date>-cg<ChemGraph>-iqc<IQC>-in<inputs>
  for each machine, over SSH:
    copy auto/ and the lock to <ROOT>/auto
    trigger.sh            nothing to do? refresh the status and stop
      build.sh            login node: venv, torch, locked packages, pip check, test sources
        verify job        compute node: verify.py, ChemGraph and IQC test suites
          promote         <machine>_chemgraph_env -> releases/<machine>/<release>
```

- **Immutable releases.** A release is built in its own directory and never
  changed. Promotion is one atomic rename of the `<machine>_chemgraph_env`
  symlink, and only after the verify job passes on a compute node.
- **Running jobs are unaffected.** `activate.sh` names the release's real
  path, so a job that sourced the environment keeps that release.
- **Failures don't spread.** A release that fails to build or verify is marked
  `FAILED` and the promoted release stays as it is. The scripts don't retry it
  until a new commit arrives (`FORCE=1` retries it).
- **Rollback** is pointing the symlink at an older release:
  `ln -sfn releases/<machine>/<release> <machine>_chemgraph_env`. The
  promoted release and the 3 newest others are kept.
- **Cheap when idle.** Without a new commit, a check is one `git ls-remote`
  per repository and one SSH command per machine; no job is submitted.

## Use

```bash
source /grand/catalyst/world-shared/chemgraph/polaris_chemgraph_env/activate.sh   # Polaris
source /lus/flare/projects/catalyst/world_shared/chemgraph/aurora_chemgraph_env/activate.sh  # Aurora
```

Sophia and Crux use `sophia_chemgraph_env` and `crux_chemgraph_env` in the
Polaris directory. Run real work on a compute node.

## Set up

On the host that runs cron (any Linux x86_64 machine with git and uv):

```bash
ssh -fN aurora; ssh -fN polaris; ssh -fN sophia; ssh -fN crux   # once per ControlPersist period
auto/update.sh                  # all machines, or: auto/update.sh polaris crux
```

SSH must reach each machine by an alias equal to its name, with a
`ControlMaster` (ALCF needs MFA; the scripts never prompt). On each machine,
uv must be installed in `~/.local/bin` or on `PATH`. Then add the cron line:

```cron
17 * * * * /path/to/chemgraph-env/auto/update.sh >> $HOME/chemgraph-env-update.log 2>&1
```

Each machine writes its status to `releases/<machine>/status.json` and to
`~/.alcf-dashboard/envs/chemgraph-<machine>.json`, which the
[ALCF Dashboard](https://github.com/keceli/ALCF-Dashboard) shows.
