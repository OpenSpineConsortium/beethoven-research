#!/usr/bin/env bash
# bee on the grid: the same accounts and the same hand-off as on a laptop, run inside the engine's
# image on a compute node. Never on the login node: start a shell on a compute node first, with one
# `srun -p <partition> --qos=<qos> -t <time> --pty bash` (an salloc shell stays on the login node).
#
#   bee-grid.sh add <label>                 list an account and sign it in, once, in the image
#   bee-grid.sh login <label>               sign a listed account in again
#   bee-grid.sh status [<label>]            claude auth status for one account or all, in the image
#   bee-grid.sh list                        the list, who rests until when, the last usage readings
#   bee-grid.sh account enable|disable|remove|spent|rested|next <args>
#                                           edit the list, as `bee account` does on a laptop
#   bee-grid.sh handoff check|where|prompt|import FILE|log [--project DIR]
#                                           the hand-off file, as `bee handoff` (FILE inside DIR)
#   bee-grid.sh run [<label>] [options] [-- claude args]
#   bee-grid.sh continue [<label>] [--fork] [options] [-- claude args]
#
#   options: --project DIR (default: the current folder), --run-dir DIR (the state folder goes
#            there instead of DIR/.claude/state), --strategy order|best|consume-first
#
# Each container sees one sign-in directory at most, bound at /claude: the one account it runs or
# asks about. `continue --fork` also binds the last session's transcript, read-only, at
# /handoff/<id>.jsonl (docs/ACCOUNTS.md section 5; what Claude Code does with it is probe M13).
# The project is bound at /work/<name>-<hash>, a path of its own. Each call's node-local temp,
# /tmp/$USER/bee_<job>_<random>, is removed on exit, the one thing this script deletes.
# Beethoven runs Claude Code and is not an Anthropic product.
set -euo pipefail

die() {
  echo "bee-grid: $*" >&2
  exit 1
}

# srun and batch scripts are job steps, whose node slurmd names; an salloc shell has a job id and
# still runs on the login node.
[ -n "${SLURM_JOB_ID:-}" ] && [ -n "${SLURMD_NODENAME:-}" ] \
  || die "run this on a compute node, in a shell from srun --pty, never on the login node"
# A Claude Code session never starts sessions or sign-ins from its own shell (--cleanenv would hide
# its markers from the check inside the image, so it is made here).
case "${1:-}" in
  add | login | run | continue)
    for _m in CLAUDECODE BEETHOVEN_REQUEST_ID ASHLEY_REQUEST_ID; do
      [ -z "${!_m:-}" ] || die "this is a Claude Code session ($_m is set); run it from your own shell"
    done
    ;;
esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$(cd "$here/../../.." && pwd)"
image="${BEE_IMAGE:-/rs/rs_grp_oschome/shared/beethoven/containers/claude-verify.sif}"
bhome="${BEETHOVEN_HOME:-$HOME/.beethoven}"
registry="$bhome/engine"

if ! command -v apptainer >/dev/null 2>&1 && type module >/dev/null 2>&1; then
  module load "${BEE_APPTAINER_MODULE:-apptainer/1.2.2}" >/dev/null 2>&1 || true
fi
command -v apptainer >/dev/null 2>&1 || die "apptainer is not on the path (module load apptainer)"
[ -f "$image" ] || die "no image at $image: build it first (containers/README.md) or set BEE_IMAGE"

# Container temp on the node's own disk, never $TMPDIR (CephFS at Wayne State), one folder per call:
# another call in the same job (a second pane, a list) must not take a live session's /tmp with it.
mkdir -p "/tmp/$USER" "$registry/usage"
chmod 700 "$registry"
scratch="$(mktemp -d "/tmp/$USER/bee_${SLURM_JOB_ID}_XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

common=(exec --containall --cleanenv --no-home --bind "$scratch:/tmp")
# bee itself, with the engine's code and the account list, and no sign-in directory.
pyargs=("${common[@]}" --bind "$engine:/engine:ro" --bind "$registry:/beethoven/engine"
  --env BEE_REGISTRY_DIR=/beethoven/engine --env PYTHONDONTWRITEBYTECODE=1)
pycmd=(env PYTHONPATH=/engine/src python -m beethoven_research.bee)
# A Claude Code session: its one sign-in, and only the usage readings of the list's folder, so the
# session can record its window but never edit the list.
sessargs=("${common[@]}" --bind "$registry/usage:/beethoven/engine/usage"
  --env BEE_REGISTRY_DIR=/beethoven/engine --env "BEE_CONTINUE_HINT=bee-grid continue")

bee_list() { apptainer "${pyargs[@]}" "$image" "${pycmd[@]}" "$@"; }

# label path -- command...: bee in the image with that one account's directory bound at /claude.
bee_with() {
  local label="$1" path="$2"
  shift 2
  apptainer "${pyargs[@]}" --bind "$path:/claude" --env CLAUDE_CONFIG_DIR=/claude \
    --env "BEE_ACCOUNT=$label" "$image" "${pycmd[@]}" "$@"
}

# line name...: split one tab-separated line into the named variables, keeping empty fields
# (`IFS=$'\t' read` would merge two tabs into one, since a tab is whitespace to read).
split_tabs() {
  local _st_rest="$1" _st_v
  shift
  for _st_v in "$@"; do
    if [[ "$_st_rest" == *$'\t'* ]]; then
      printf -v "$_st_v" '%s' "${_st_rest%%$'\t'*}"
      _st_rest="${_st_rest#*$'\t'}"
    else
      printf -v "$_st_v" '%s' "$_st_rest"
      _st_rest=""
    fi
  done
}

account_path() {
  local row l p e
  while IFS= read -r row; do
    split_tabs "$row" l p e
    if [ "$l" = "$1" ]; then
      printf '%s\n' "$p"
      return 0
    fi
  done < <(bee_list account rows)
  return 1
}

check_label() {
  # Spelled out: bash 4.2 matches [a-z] by the locale's collation, which takes in capitals too.
  case "$1" in
    "" | -* | *[!abcdefghijklmnopqrstuvwxyz0123456789-]*)
      die "a label is lower-case letters, digits and hyphens"
      ;;
  esac
  [ "${#1}" -le 32 ] || die "a label is at most 32 characters"
}

cmd_add() {
  local label="${1:-}" dir path
  check_label "$label"
  dir="$bhome/accounts/$label/claude"
  if [ "$label" = main ] && [ -d "$bhome/claude" ] && [ ! -e "$dir" ] && [ ! -L "$dir" ]; then
    # The launcher's own sign-in is the account main: linked from here, nothing moved.
    mkdir -p "$(dirname "$dir")"
    ln -s "$bhome/claude" "$dir"
  elif [ ! -L "$dir" ]; then
    mkdir -p "$dir"
    chmod 700 "$dir"
  fi
  if ! account_path "$label" >/dev/null; then
    bee_list account add "$label" --path "$dir" --no-login --no-create
  fi
  path="$(account_path "$label")"
  bee_with "$label" "$path" account login "$label" --bound /claude
}

cmd_login() {
  local label="${1:-}" path
  check_label "$label"
  path="$(account_path "$label")" || die "no account named $label; bee-grid.sh add $label"
  bee_with "$label" "$path" account login "$label" --bound /claude
}

cmd_status() {
  local row l p e bad=0
  while IFS= read -r row; do
    split_tabs "$row" l p e
    if [ -n "${1:-}" ] && [ "$l" != "$1" ]; then continue; fi
    if [ -z "$p" ]; then
      echo "$l: no directory recorded" >&2
      bad=1
      continue
    fi
    bee_with "$l" "$p" account status "$l" --bound /claude || bad=1
  done < <(bee_list account rows)
  return "$bad"
}

# dir: where the project is bound, a path of its own, so what Claude Code keeps per folder (the
# trust answer, approved servers) belongs to that project and not to every project seen at /work.
work_path() {
  local sum name="${1##*/}"
  sum="$(printf '%s' "$1" | sha256sum)"
  printf '/work/%s-%s' "$(printf '%s' "$name" | LC_ALL=C tr -c 'A-Za-z0-9._-' '_')" "${sum:0:8}"
}

cmd_session() {
  local mode="$1" label="" fork="" project="$PWD" rundir="" strategy=order
  shift
  local -a extra=() binds=() envs=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --fork) fork=1 ;;
      --project) project="${2:?}"; shift ;;
      --run-dir) rundir="${2:?}"; shift ;;
      --strategy) strategy="${2:?}"; shift ;;
      --) shift; extra=("$@"); break ;;
      -*) die "unknown option $1" ;;
      *) label="$1" ;;
    esac
    shift
  done
  project="$(cd "$project" && pwd)"
  local work statedir="$project/.claude/state"
  work="$(work_path "$project")"
  binds=(--bind "$project:$work" --pwd "$work")
  if [ -n "$rundir" ]; then
    mkdir -p "$rundir"
    rundir="$(cd "$rundir" && pwd)"
    binds+=(--bind "$rundir:/run_dir")
    envs+=(--env BEETHOVEN_RUN_ROOT=/run_dir)
    statedir="$rundir/state"
  fi
  local tries=0 line t_label t_path t_mode t_transcript t_prompt t_owner rc started ans src owner
  while :; do
    tries=$((tries + 1))
    [ "$tries" -le 10 ] || die "no account could start after ten tries; bee-grid.sh list"
    local -a plan=(plan "$mode")
    [ -n "$label" ] && plan+=("$label")
    plan+=(--project "$work" --strategy "$strategy")
    [ -n "$fork" ] && plan+=(--fork)
    # Step one: choose the account and the mode, with no sign-in directory bound.
    line="$(apptainer "${pyargs[@]}" "${binds[@]}" ${envs[@]+"${envs[@]}"} "$image" \
      "${pycmd[@]}" "${plan[@]}")" || exit 1
    line="${line##*$'\n'}" # the plan is one line; bee's notes went to the terminal
    split_tabs "$line" t_label t_path t_mode t_transcript t_prompt t_owner
    [ -n "$t_path" ] || die "$t_label has no directory recorded"
    # Step two: its sign-in, asked where its directory is bound.
    if ! bee_with "$t_label" "$t_path" account status "$t_label" --bound /claude; then
      [ -z "$label" ] || die "$t_label is not signed in; bee-grid.sh login $t_label"
      bee_list account disable "$t_label"
      continue
    fi
    local -a more=() cmd=(claude) sess=(--env BEETHOVEN_HANDOFF_GATE=1)
    if [ "$t_mode" = fork ]; then
      # The plan read the path from a record the project's session could write, so the file is
      # resolved here, on the host, and bound only if it is a session file in the writing account's
      # own projects folder.
      src="$(readlink -f -- "$t_transcript" 2>/dev/null)" || src=""
      owner="$(readlink -f -- "$t_owner" 2>/dev/null)" || owner=""
      [ -n "$src" ] && [ -f "$src" ] \
        || die "the last transcript ($t_transcript) is not there; continue without --fork"
      case "$src" in
        "$owner"/projects/*/*.jsonl) [ -n "$owner" ] || src="" ;;
        *) src="" ;;
      esac
      [ -n "$src" ] || die "the last transcript is not a session file of its account; continue without --fork"
      more=(--bind "$src:/handoff/${src##*/}:ro")
      cmd+=(--resume "/handoff/${src##*/}" --fork-session)
    fi
    [ "$t_mode" = file ] && sess+=(--env BEETHOVEN_HANDOFF=1)
    [ -n "$t_prompt" ] && cmd+=("$t_prompt")
    cmd+=(${extra[@]+"${extra[@]}"})
    echo "Starting Claude Code as $t_label ($t_mode) in $project" >&2
    started="$(date +%s)"
    set +e
    apptainer "${sessargs[@]}" "${binds[@]}" ${envs[@]+"${envs[@]}"} ${more[@]+"${more[@]}"} \
      "${sess[@]}" --bind "$t_path:/claude" --env CLAUDE_CONFIG_DIR=/claude \
      --env "BEE_ACCOUNT=$t_label" "$image" "${cmd[@]}"
    rc=$?
    set -e
    # A usage limit this session recorded (the StopFailure hook) and not yet acted on: offer the
    # next account, at a terminal only.
    if [ -t 0 ] && [ -f "$statedir/limit.json" ] \
      && [ "$(stat -c %Y "$statedir/limit.json")" -ge "$started" ] \
      && ! grep -q '"handled"' "$statedir/limit.json"; then
      read -r -p "$t_label reached its usage limit. Continue on the next account? [Y/n] " ans || ans=n
      case "${ans:-y}" in
        [Yy]*) mode=continue; label=""; fork=""; tries=0; continue ;;
      esac
    fi
    return "$rc"
  done
}

# The list's own edits, which need no sign-in directory.
cmd_account() {
  case "${1:-}" in
    list | enable | disable | remove | spent | rested | next | path) bee_list account "$@" ;;
    *) die "account ${1:-}: use add, login or status for what needs the sign-in" ;;
  esac
}

# bee handoff with the project bound as a session sees it; a file to import sits inside the project.
cmd_handoff() {
  local project="$PWD" a work
  local -a args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --project) project="${2:?}"; shift ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  project="$(cd "$project" && pwd)"
  work="$(work_path "$project")"
  if [ "${args[0]:-}" = import ] && [ -n "${args[1]:-}" ] && [ "${args[1]}" != - ]; then
    a="$(cd "$(dirname "${args[1]}")" && pwd)/$(basename "${args[1]}")"
    case "$a" in
      "$project"/*) args[1]="$work/${a#"$project"/}" ;;
      *) die "put the file inside the project folder first ($project)" ;;
    esac
  fi
  apptainer "${pyargs[@]}" --bind "$project:$work" --pwd "$work" "$image" "${pycmd[@]}" \
    handoff ${args[@]+"${args[@]}"} --project "$work"
}

sub="${1:-}"
[ $# -gt 0 ] && shift
case "$sub" in
  add) cmd_add "$@" ;;
  login) cmd_login "$@" ;;
  status) cmd_status "$@" ;;
  list) bee_list account list ;;
  account) cmd_account "$@" ;;
  handoff) cmd_handoff "$@" ;;
  run | continue) cmd_session "$sub" "$@" ;;
  *) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
