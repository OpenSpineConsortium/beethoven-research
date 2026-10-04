# The substrate image

`claude-verify.def` builds the one image every engine job runs in: Ubuntu 24.04, Node 22, the Claude
Code CLI, and this repository's pixi environment reconstructed from `pixi.lock` with
`pixi install --locked`. The image is the *substrate*; the engine's code is bound into it at run time
at a pinned git SHA, so the image hash identifies the environment and the Run ID identifies the code.

## Build

On the cluster, inside a Slurm job on a compute node (never the login node):

```sh
module load apptainer/1.2.2           # Wayne State; other sites differ
bash containers/build.sh --out /rs/rs_grp_oschome/shared/beethoven/containers
```

`build.sh` uses `--fakeroot`, keeps temp and cache on the node's local disk under `/tmp/$USER` with
a cleanup trap (never `$TMPDIR`, which on Wayne State is CephFS), and writes `claude-verify.digest`
beside the image: the sha256 of the `.sif`, the apptainer, Claude Code and pixi versions read back
from inside the image, the recipe's git SHA and the lock's sha256. **Commit the digest file** when an
image is published; the `.sif` itself is never committed.

**Status:** the recipe builds in CI (lane `image (apptainer build)`; first run 2026-10-04: apptainer
1.3.6 from Ubuntu's archive, about two minutes, Claude Code 2.1.289 landed, image sha256
`b8c07c3adcc9f2f53fd7ab5d8d2289b8bd2407b0e3b0fca88a458154c57408a1`, a runner's image that is
discarded with the job). **No grid build yet and no committed digest**: the first build on a compute
node, with its digest committed beside the recipe, is Linear BEE-103's acceptance.

## Run

Always the three flags, so a job sees nothing of the host it was not given on purpose:

```sh
apptainer exec --containall --cleanenv --no-home \
    --bind "$CHECKOUT":/work \
    --bind "$HOME/.beethoven/claude":/claude:ro \
    --env CLAUDE_CONFIG_DIR=/claude \
    --env TMPDIR=/tmp/$USER/hpcworker_$SLURM_JOB_ID \
    claude-verify.sif pixi run --manifest-path /opt/beethoven-research/pixi.toml gate
```

- `--containall --cleanenv --no-home`: no host environment variables, no home directory, no stray
  mounts. The recipe's `%environment` additionally unsets every credential variable it knows of.
- The student's own Claude sign-in directory is bound **read-only** and named by
  `CLAUDE_CONFIG_DIR`. The engine starts Claude Code there and reads `claude auth status`; it never
  reads, copies or relays what is inside (CLAUDE.md, non-negotiables).
- Node-local scratch is bound explicitly; nothing of the job's runtime state goes to the parallel
  filesystem while it runs (Goal 5).

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.
