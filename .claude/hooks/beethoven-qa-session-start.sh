#!/bin/bash
# SessionStart hook: give a Claude Code cloud session this repository's pixi environment, so
# `pixi run --locked gate` works there the way it does on a laptop or in CI. Outside a cloud
# session it exits at once (the laptop's own pixi is the developer's business). Idempotent: it
# skips what is already in place, since hooks run on every start and resume. It never exits
# non-zero: a missing environment is reported, not turned into a session that cannot start.
#
# Scope, from the cloud-environments documentation: a session with ONE repository runs this hook
# from .claude/settings.json; a project thread with several repositories does not read it, and
# needs these same commands pasted as the environment's setup script instead. In Anthropic-hosted
# sessions GitHub release assets reach only repositories attached to the session, so the pixi
# download below can be refused; then the message says what to do, and nothing else is tried.
set -uo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && [ "${BEETHOVEN_SETUP_FORCE:-}" != "1" ]; then
    exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# The gate runs before every push (.githooks/pre-push), in a cloud session as on a laptop: set
# first, before anything below can fail, so a session whose pixi never arrives still refuses a
# push rather than making one with no gate.
(cd "$ROOT" && git config core.hooksPath .githooks) || true
# The pixi version the committed pixi.lock was produced with, and the sha256 of its release asset
# pixi-x86_64-unknown-linux-musl: this repository's own measurement of 2026-10-08, from two
# downloads at different times, equal to the binary the session that pinned the lock runs; pixi's
# release publishes no checksum asset to compare with. A new pixi version means a new digest,
# measured again the same way.
PIXI_VERSION=0.81.0
PIXI_SHA256=248510f5f75460a2ae4ec62d761e15c0191dc35db3b1da6f82d9f17c6017051d
BIN="$HOME/.pixi/bin"
mkdir -p "$BIN"

if ! "$BIN/pixi" --version 2>/dev/null | grep -q "$PIXI_VERSION"; then
    url="https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}/pixi-x86_64-unknown-linux-musl"
    # The digest is checked before the file is made executable or moved into place; a file that
    # is not the one measured is removed, like one that did not arrive.
    if ! curl -fsSL -o "$BIN/pixi.tmp" "$url"; then
        rm -f "$BIN/pixi.tmp"
        echo "session-start: could not fetch pixi ${PIXI_VERSION} from its release asset (refused by the" \
             "session's network?). The gate needs it: install pixi ${PIXI_VERSION}, then 'pixi install --locked'."
        exit 0
    elif ! echo "$PIXI_SHA256  $BIN/pixi.tmp" | sha256sum --status -c -; then
        rm -f "$BIN/pixi.tmp"
        echo "session-start: the pixi ${PIXI_VERSION} fetched is not the one this repository measured" \
             "(sha256 differs); it was removed. The gate needs it: install pixi ${PIXI_VERSION} yourself," \
             "then 'pixi install --locked'."
        exit 0
    fi
    chmod 0755 "$BIN/pixi.tmp" && mv "$BIN/pixi.tmp" "$BIN/pixi"
fi
export PATH="$BIN:$PATH" PIXI_NO_PATH_UPDATE=1

# --locked: reconstruct from the lock, never re-solve; a drifted manifest is an error worth seeing.
if ! (cd "$ROOT" && pixi install --locked); then
    echo "session-start: 'pixi install --locked' failed in $ROOT; the gate is not runnable until it succeeds."
    exit 0
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    {
        echo "export PATH=\"$BIN:\$PATH\""
        echo "export PIXI_NO_PATH_UPDATE=1"
    } >> "$CLAUDE_ENV_FILE"
fi
echo "session-start: $(pixi --version) and the locked environment are ready; run 'pixi run --locked gate'."
exit 0
