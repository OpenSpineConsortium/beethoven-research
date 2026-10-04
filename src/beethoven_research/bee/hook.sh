#!/usr/bin/env bash
# Runs one of bee's hooks from an engine checkout (a laptop, a cloud session, a grid checkout used
# outside the image), as `bee init` writes them into a project's .claude/settings.local.json:
#
#   bash "<engine>/src/beethoven_research/bee/hook.sh" session-start|post-tool-use|pre-compact|
#                                                      stop|stop-failure|statusline|claudeignore
#
# It runs the hook with the checkout's own pixi environment (Linux and macOS: bin/python; Windows,
# where Claude Code runs hooks under Git Bash: python.exe). Without that environment it drains the
# event and steps aside, so a project set up by `bee init` never breaks when the engine is missing.
# Inside the image the managed settings name the baked copies instead (containers/).
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine="$(cd "$here/../../.." && pwd)"
name="${1:-}"
for py in "$engine/.pixi/envs/default/bin/python" "$engine/.pixi/envs/default/python.exe"; do
  if [ -f "$py" ]; then
    case "$name" in
      claudeignore) exec "$py" "$here/claudeignore.py" ;;
      *) exec "$py" "$here/handoff.py" "$name" ;;
    esac
  fi
done
cat >/dev/null
exit 0
