#!/bin/bash
# Build the substrate image from containers/claude-verify.def and record what was built.
#
# Where it runs: inside a Slurm allocation on a compute node, with --fakeroot (the student has no
# root on the cluster). It refuses to run outside Slurm unless --allow-outside-slurm is given, which
# is for CI runners and nothing else: nothing container-related ever runs on the login node.
#
# Temp and cache go to the compute node's local disk under /tmp/$USER, with a cleanup trap. $TMPDIR
# is deliberately not trusted: on Wayne State it points at CephFS, not the node.
#
# What it records, beside the image, in <image>.digest: the sha256 of the .sif, the apptainer
# version, the Claude Code and pixi versions that landed inside, the git SHA of the recipe and the
# lock, and the UTC time. That file is the number to cite; nothing in the recipe is.
set -euo pipefail

usage() { echo "usage: $0 [--out DIR] [--allow-outside-slurm]" >&2; exit 2; }

OUT=""
ALLOW_OUTSIDE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --allow-outside-slurm) ALLOW_OUTSIDE=1; shift ;;
        -h|--help) usage ;;
        *) usage ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DEF="$HERE/claude-verify.def"
NAME="claude-verify"

if [ -z "${SLURM_JOB_ID:-}" ] && [ "$ALLOW_OUTSIDE" -ne 1 ]; then
    echo "$0: not inside a Slurm job; builds run on a compute node, never the login node" >&2
    echo "       (sbatch a job that runs this script; --allow-outside-slurm is for CI only)" >&2
    exit 1
fi
command -v apptainer >/dev/null 2>&1 || { echo "$0: apptainer not found (module load apptainer?)" >&2; exit 1; }

# Node-local scratch for the build; never $TMPDIR, never the login node's /tmp.
SCRATCH="/tmp/${USER:-nobody}/beethoven_build_${SLURM_JOB_ID:-local$$}"
mkdir -p "$SCRATCH"
trap 'rm -rf "$SCRATCH"' EXIT
export APPTAINER_TMPDIR="$SCRATCH/tmp" APPTAINER_CACHEDIR="$SCRATCH/cache"
mkdir -p "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"

OUT="${OUT:-$HERE}"
mkdir -p "$OUT"
SIF="$OUT/$NAME.sif"

# --fakeroot inside Slurm; under CI the runner is root and the flag is not needed.
FAKEROOT=()
if [ "$(id -u)" -ne 0 ]; then FAKEROOT=(--fakeroot); fi

# The recipe copies pixi.toml and pixi.lock by relative path, so build from the repository root.
cd "$ROOT"
apptainer build "${FAKEROOT[@]}" --force "$SIF" "$DEF"

# Read the versions back from the image rather than trusting what the recipe asked for.
CLAUDE_VERSION="$(apptainer exec --containall --cleanenv --no-home "$SIF" claude --version 2>/dev/null | head -1 || echo unknown)"
PIXI_VERSION="$(apptainer exec --containall --cleanenv --no-home "$SIF" pixi --version 2>/dev/null | head -1 || echo unknown)"
GIT_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
SHA256="$(sha256sum "$SIF" | cut -d' ' -f1)"

{
    echo "image: $NAME.sif"
    echo "sha256: $SHA256"
    echo "built_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "apptainer: $(apptainer --version)"
    echo "claude_code: $CLAUDE_VERSION"
    echo "pixi: $PIXI_VERSION"
    echo "recipe_git_sha: $GIT_SHA"
    echo "pixi_lock_sha256: $(sha256sum "$ROOT/pixi.lock" | cut -d' ' -f1)"
    echo "slurm_job: ${SLURM_JOB_ID:-none}"
    echo "node: $(hostname)"
} > "$OUT/$NAME.digest"

echo "built $SIF"
cat "$OUT/$NAME.digest"
