#!/usr/bin/env bash
# Claude Code runs this after every Edit or Write (hooks.json) with the call as JSON on stdin.
# `python -m beethoven_qa coach`, from the project's own pinned environment, answers with what the
# code standards would block in CI in the edited file, which Claude Code puts beside the edit, or
# with nothing. The limits are the ones the project pins, so the answer is CI's own.
#
# Coaching never stands in a session's way: whatever cannot run here ends quietly with exit 0 and
# no output (a project with no pixi.toml or no installed environment, pixi not found, a pinned copy
# older than the coach command, a tool that fails). No environment is installed or re-solved from a
# hook (`--as-is`): an install takes minutes and belongs to the session's start, never to an edit.
set -u
root="${CLAUDE_PROJECT_DIR:-}"
[ -n "$root" ] && [ -f "$root/pixi.toml" ] && [ -d "$root/.pixi/envs/default" ] || exit 0
pixi="$(command -v pixi || true)"
[ -n "$pixi" ] || pixi="$HOME/.pixi/bin/pixi"
[ -x "$pixi" ] || exit 0
cd "$root" || exit 0
answer="$("$pixi" run --as-is python -m beethoven_qa coach --root . 2>/dev/null)" || exit 0
[ -z "$answer" ] || printf '%s\n' "$answer"
exit 0
