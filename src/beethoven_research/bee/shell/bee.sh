# bee for bash and zsh: a laptop (Linux, macOS, Git Bash or WSL on Windows) or a compute node.
# Source it from ~/.bashrc or ~/.zshrc:
#
#   . /path/to/beethoven-research/src/beethoven_research/bee/shell/bee.sh
#
#   bee <args>        the bee command, from the engine checkout's pixi environment
#   bee-use <label>   switch this shell to an account: CLAUDE_CONFIG_DIR and BEE_ACCOUNT, nothing else
#   bee-which         which account this shell is on
#
# Unlike the guide's helper this never sets CLAUDE_CONFIG_DIR to ~/.claude for the default
# account: it unsets it, because with the variable set Claude Code keeps .claude.json inside the
# directory and the default account's own ~/.claude.json would be left behind
# (docs/bee/STATE_HANDOFF.md). Beethoven runs Claude Code and is not an Anthropic product.

if [ -n "${BASH_VERSION:-}" ]; then
  _bee_here="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
  eval '_bee_here="${(%):-%x}"'
else
  _bee_here="$0"
fi
BEE_ENGINE="${BEE_ENGINE:-$(cd "$(dirname "$_bee_here")/../../../.." && pwd)}"
export BEE_ENGINE
unset _bee_here

bee() {
  local py src
  if [ -f "$BEE_ENGINE/.pixi/envs/default/bin/python" ]; then
    py="$BEE_ENGINE/.pixi/envs/default/bin/python"
  elif [ -f "$BEE_ENGINE/.pixi/envs/default/python.exe" ]; then
    py="$BEE_ENGINE/.pixi/envs/default/python.exe"
  else
    echo "bee: run 'pixi install --locked' in $BEE_ENGINE first" >&2
    return 1
  fi
  src="$BEE_ENGINE/src"
  # A Windows python under Git Bash reads C:\... paths, not /c/...
  if command -v cygpath >/dev/null 2>&1; then src="$(cygpath -w "$src")"; fi
  PYTHONPATH="$src" "$py" -m beethoven_research.bee "$@"
}

bee-use() {
  if [ "$#" -ne 1 ]; then
    echo "usage: bee-use <label>" >&2
    return 2
  fi
  local lines
  lines="$(bee account env "$1")" || return $?
  eval "$lines"
  echo "This shell now uses the account $1 ($(_bee_place))." >&2
}

bee-which() {
  echo "${BEE_ACCOUNT:-no account chosen} ($(_bee_place))"
}

# An apostrophe inside "${VAR:-...}" opens a quote in some bash versions, so the words live here.
_bee_place() {
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    printf '%s' "$CLAUDE_CONFIG_DIR"
  else
    printf '%s' "Claude Code's default place"
  fi
}
