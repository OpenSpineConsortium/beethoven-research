"""``bee``: switch Claude accounts and carry the work across, on a laptop and on the grid.

    bee account add <label>          sign one account in, once, in its own directory
    bee account list | status        the accounts, their sign-in and their last usage reading
    bee account env <label>          lines a shell evaluates to switch (posix or PowerShell)
    bee run [<label>]                start Claude Code on an account, with the hand-off hooks on
    bee continue [<label>] [--fork]  hand the work to the next account (or the one named)
    bee handoff check|prompt|import  the hand-off file: report, the extraction prompt, a paste
    bee init                         set a project up: hooks, /handoff, status line, .claudeignore

The account part is docs/ACCOUNTS.md; the hand-off part is docs/bee/STATE_HANDOFF.md, which also
says which steps of greg's guide were changed and why. On the grid the same commands run inside the
image through ``bee-grid.sh`` beside this file, on a compute node, never the login node.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from beartype import beartype
from icontract import ensure

from beethoven_research.bee import accounts as acc
from beethoven_research.bee import claudeignore, handoff

__all__ = ["main"]

# src/beethoven_research/bee/cli.py: the engine checkout is three folders up.
ENGINE_ROOT = Path(__file__).resolve().parents[3]
HOOK_SCRIPT = "src/beethoven_research/bee/hook.sh"
HOOK_EVENTS: tuple[tuple[str, str, str, bool], ...] = (
    # (event, matcher, hook name, async)
    ("SessionStart", "startup|resume|compact|fork|clear", "session-start", False),
    ("PostToolUse", "Edit|Write|MultiEdit|NotebookEdit|Bash", "post-tool-use", True),
    ("PreCompact", "manual|auto", "pre-compact", False),
    ("Stop", "", "stop", False),
    ("StopFailure", "", "stop-failure", False),
)
IGNORE_MATCHER = "Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob|LS"
IGNORE_TEMPLATE = """# What Claude Code's file tools leave alone here (gitignore patterns).
# Enforced by Beethoven's PreToolUse hook; secrets belong in the deny rules as well.

# Build outputs and generated files
node_modules/
build/
dist/
.next/
out/
.pixi/

# Environment and secrets
.env*
*.pem
*.key
credentials.json

# Large media and databases
*.mp4
*.sqlite3
*.db

# Logs and caches
logs/
*.log
.cache/
"""
COMMAND_HANDOFF = """---
description: Write the Beethoven hand-off file so another account or machine can continue
---
Write the hand-off file now. The Beethoven note in this session's context names the file and the
instructions (EXTRACT.md in the state folder; `.claude/state/EXTRACT.md` when no note is there).
Read those instructions and follow them exactly: the six-section schema, paths that exist on disk,
exact error strings, 3 to 5 next actions, under 8 KB, no secrets. $ARGUMENTS
"""
COMMAND_HYDRATE = """---
description: Continue from the Beethoven hand-off file left by another session
---
Read HYDRATE.md in the Beethoven state folder named in this session's context
(`.claude/state/HYDRATE.md` when no note is there) and follow it, in order: validate the hand-off
against the disk before you trust it, verify the blockers, then act. $ARGUMENTS
"""
ENVRC_BEGIN = "# >>> bee account (written by bee account pin) >>>"
ENVRC_END = "# <<< bee account <<<"
INIT_RECORD = "init.json"  # the deny rules bee init made from .claudeignore, in the state folder
# continue --fork believes the recorded transcript only for a file named after the recorded session
# in its account's own projects folder: the record sits in the project, which a session can write.
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}")
# Project settings that would point a session at a key, a helper that prints one, or a cloud
# provider, instead of the person's own subscription (usage credits are never used).
_PROJECT_CREDENTIAL_KEYS = (
    "apiKeyHelper",
    "awsCredentialExport",
    "awsAuthRefresh",
    "gcpAuthRefresh",
)


class Refusal(Exception):
    """A plain-words reason the command will not go on."""


def _say(text: str) -> None:
    print(text, flush=True)


def _project(arg: str | None) -> str:
    return os.path.abspath(arg or os.getcwd())


def _registry() -> tuple[str, tuple[acc.Account, ...]]:
    directory = acc.registry_dir()
    return directory, acc.load_accounts(directory)


def _find(accounts: tuple[acc.Account, ...], label: str) -> acc.Account:
    for a in accounts:
        if a.label == label:
            return a
    known = ", ".join(a.label for a in accounts) or "none yet"
    raise Refusal(f"no account named {label} (known: {known}); bee account add {label}")


def _claude() -> str:
    found = acc.find_claude()
    if found is None:
        raise Refusal("claude is not on the path here; install Claude Code first")
    return found


def _ensure_not_login_node() -> None:
    why = acc.login_node_refusal()
    if why:
        raise Refusal(why)


def _ensure_not_nested() -> None:
    """bee starts Claude Code and signs accounts in; a Claude Code session never does either from
    its own shell (an agent marker in the environment means one is asking)."""
    for marker in ("CLAUDECODE", "BEETHOVEN_REQUEST_ID", "ASHLEY_REQUEST_ID"):
        if os.environ.get(marker):
            raise Refusal(
                f"this is a Claude Code session ({marker} is set); run bee from your own terminal"
            )


def _when(epoch: int) -> str:
    return acc.utc_text(epoch) if epoch else "-"


# --- bee account ---------------------------------------------------------------------------------


def _cmd_add(args: argparse.Namespace) -> int:
    label = args.label
    if not acc.valid_label(label):
        raise Refusal("a label is lower-case letters, digits and hyphens, at most 32 characters")
    directory, accounts = _registry()
    if any(a.label == label for a in accounts):
        raise Refusal(f"{label} is already listed; bee account status {label}")
    if args.default_location:
        path: str | None = None
    elif args.path:
        path = args.path if args.no_create else os.path.abspath(os.path.expanduser(args.path))
        if not args.no_create:
            os.makedirs(path, mode=0o700, exist_ok=True)
    else:
        path = acc.prepare_account_dir(label)
    if path in [a.path for a in accounts]:
        raise Refusal(f"that directory is already the account {_owner(accounts, path)}")
    same = [a.label for a in accounts if path and a.path and _same_dir(a.path, path)]
    if same:
        # main is a link to the launcher's sign-in; listing that under a second label would hand a
        # spent window to itself.
        raise Refusal(f"that directory is already the account {same[0]} (through a link)")
    accounts = acc.add_account(accounts, label, path)
    acc.save_accounts(directory, accounts)
    where = path or "Claude Code's default place (CLAUDE_CONFIG_DIR unset)"
    _say(f"Listed {label}: {where}")
    if args.no_login:
        _say(f"Sign it in when you are ready: bee account login {label}")
        return 0
    return _login(_find(accounts, label))


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _epoch(text: str) -> int | None:
    try:
        stamp = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(stamp.timestamp()) if stamp.tzinfo else None


def _owner(accounts: tuple[acc.Account, ...], path: str | None) -> str:
    return next((a.label for a in accounts if a.path == path), "?")


def _login(account: acc.Account) -> int:
    _ensure_not_nested()
    _ensure_not_login_node()
    claude = _claude()
    verdict, _ = acc.run_status(account, claude)
    if verdict.ok:
        _say(f"{account.label} is already signed in; nothing to do.")
        return 0
    _say(
        f"Signing {account.label} in. Claude Code prints a link: open it in a browser signed in "
        f"to the Claude account you want as {account.label}, approve, and paste the code back "
        "here. Beethoven never sees it."
    )
    rc = acc.run_login(account, claude)
    verdict, shown = acc.run_status(account, claude)
    who = f" ({shown['email']})" if shown and shown.get("email") else ""
    _say(f"{account.label}: {verdict.reason}{who}")
    return 0 if verdict.ok else (rc or 1)


def _bound(account: acc.Account, where: str | None) -> acc.Account:
    """Inside the image the account's directory is bound at ``where`` (``/claude``): ask there."""
    return replace(account, path=where) if where else account


def _cmd_login(args: argparse.Namespace) -> int:
    _, accounts = _registry()
    return _login(_bound(_find(accounts, args.label), args.bound))


def _cmd_list(args: argparse.Namespace) -> int:
    directory, accounts = _registry()
    usage = {u.label: u for u in acc.load_usage(directory, accounts)}
    t = acc.now()
    rows: list[dict[str, object]] = []
    for a in sorted(accounts, key=lambda x: x.order):
        u = usage.get(a.label)
        live = u is not None and u.five_hour_resets_at > t
        rows.append(
            {
                "label": a.label,
                "path": a.path,
                "enabled": a.enabled,
                "order": a.order,
                "resting_until": _when(a.hibernate_until) if a.hibernate_until > t else None,
                "five_hour_pct": u.five_hour_pct if u and live else None,
                "five_hour_resets": _when(u.five_hour_resets_at) if u and live else None,
                "seven_day_pct": u.seven_day_pct if u and u.seven_day_resets_at > t else None,
            }
        )
    if args.json:
        _say(json.dumps(rows, indent=2))
        return 0
    if not rows:
        _say("No accounts yet. bee account add main")
        return 0
    for r in rows:
        state = "on" if r["enabled"] else "off"
        if r["resting_until"]:
            state += f", resting until {r['resting_until']}"
        window = ""
        if r["five_hour_pct"] is not None:
            window = f"  5h {r['five_hour_pct']}% until {r['five_hour_resets']}"
        if r["seven_day_pct"] is not None:
            window += f"  7d {r['seven_day_pct']}%"
        _say(f"{r['label']:<12} {state:<34} {r['path'] or '(default place)'}{window}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    _ensure_not_login_node()
    _, accounts = _registry()
    chosen = [_find(accounts, args.label)] if args.label else list(accounts)
    if args.bound:
        if not args.label:
            raise Refusal("--bound names one account's directory; give its label")
        chosen = [_bound(chosen[0], args.bound)]
    if not chosen:
        _say("No accounts yet. bee account add main")
        return 1
    claude = _claude()
    bad = 0
    for a in chosen:
        verdict, shown = acc.run_status(a, claude)
        details = [shown[k] for k in ("email", "subscriptionType") if shown and k in shown]
        extra = f" ({', '.join(details)})" if details else ""
        _say(f"{a.label:<12} {verdict.reason}{extra}")
        bad += 0 if verdict.ok else 1
    return 0 if bad == 0 else 1


def _cmd_enable(args: argparse.Namespace, on: bool) -> int:
    directory, accounts = _registry()
    _find(accounts, args.label)
    acc.save_accounts(directory, acc.set_enabled(accounts, args.label, on))
    _say(f"{args.label} is {'back in' if on else 'out of'} the rotation; nothing was signed out.")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    directory, accounts = _registry()
    a = _find(accounts, args.label)
    acc.save_accounts(directory, acc.remove_account(accounts, args.label))
    where = a.path or "the default place"
    _say(
        f"{args.label} is off the list. Its directory ({where}) was not touched and stays signed "
        "in; delete it yourself if you mean to."
    )
    return 0


def _switch_pairs(account: acc.Account) -> tuple[tuple[str, str | None], ...]:
    """The account's two variables and every credential variable unset: a plain ``claude`` after
    bee-use or a pinned .envrc runs on that directory's sign-in, not on a token left behind."""
    return acc.env_pairs(account) + tuple((name, None) for name in acc.CREDENTIAL_VARS)


def _cmd_env(args: argparse.Namespace) -> int:
    _, accounts = _registry()
    sys.stdout.write(acc.render_env(_switch_pairs(_find(accounts, args.label)), args.shell))
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    _, accounts = _registry()
    a = _find(accounts, args.label)
    _say(a.path or "")
    return 0


def _cmd_rows(args: argparse.Namespace) -> int:
    """Tab-separated rows for shell scripts (bee-grid.sh): label, path, enabled."""
    _, accounts = _registry()
    for a in sorted(accounts, key=lambda x: x.order):
        sys.stdout.write(f"{a.label}\t{a.path or ''}\t{'1' if a.enabled else '0'}\n")
    return 0


def _cmd_next(args: argparse.Namespace) -> int:
    directory, accounts = _registry()
    current = args.from_label or os.environ.get("BEE_ACCOUNT", "")
    usage = acc.load_usage(directory, accounts)
    label = acc.choose_next(accounts, current, acc.now(), args.strategy, usage, args.threshold)
    if not label:
        _say("")
        return 1
    _say(label)
    return 0


def _cmd_spent(args: argparse.Namespace) -> int:
    directory, accounts = _registry()
    _find(accounts, args.label)
    t = acc.now()
    if args.hours is not None:
        until = t + int(args.hours * 3600)
    else:
        usage = {u.label: u for u in acc.load_usage(directory, accounts)}
        u = usage.get(args.label)
        until = acc.hibernate_until(
            t,
            u.five_hour_resets_at if u else 0,
            u.seven_day_pct if u else 0,
            u.seven_day_resets_at if u else 0,
        )
    acc.save_accounts(directory, acc.set_hibernation(accounts, args.label, until))
    acc.log_switch(directory, f"{acc.utc_text(t)} rest account={args.label} until={_when(until)}")
    _say(f"{args.label} rests until {_when(until)}.")
    return 0


def _cmd_rested(args: argparse.Namespace) -> int:
    directory, accounts = _registry()
    _find(accounts, args.label)
    acc.save_accounts(directory, acc.set_hibernation(accounts, args.label, 0))
    _say(f"{args.label} is awake.")
    return 0


def _cmd_pin(args: argparse.Namespace, pin: bool) -> int:
    project = _project(args.project)
    envrc = os.path.join(project, ".envrc")
    try:
        with open(envrc, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    kept = _strip_block(text)
    kept = kept.rstrip("\n") + "\n" if kept.strip() else ""  # the separator goes with the block
    if pin:
        _, accounts = _registry()
        lines = acc.render_env(_switch_pairs(_find(accounts, args.label)), "posix")
        kept = (kept + "\n" if kept else "") + (f"{ENVRC_BEGIN}\n{lines}{ENVRC_END}\n")
    with open(envrc, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(kept)
    if pin:
        _say(f"{project} now uses {args.label} under direnv (run: direnv allow).")
    else:
        _say(f"{project} no longer pins an account.")
    return 0


def _strip_block(text: str) -> str:
    out: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        if line.strip() == ENVRC_BEGIN:
            inside = True
            continue
        if line.strip() == ENVRC_END:
            inside = False
            continue
        if not inside:
            out.append(line)
    if inside:
        # Without its closing line the block would run to the end and take the person's own
        # lines with it; .envrc is theirs, so it is left as it is.
        raise Refusal(
            f"the .envrc has bee's opening line but not its closing line ({ENVRC_END}); "
            "fix it by hand first"
        )
    return "".join(out)


# --- bee run and bee continue --------------------------------------------------------------------


def _absorb_limit(
    project: str, directory: str, accounts: tuple[acc.Account, ...]
) -> tuple[tuple[acc.Account, ...], str]:
    """If the last session recorded a usage limit not yet acted on, rest that account until its
    window resets and mark the record handled. Returns the accounts and the label that hit it."""
    sdir = handoff.state_dir(project)
    limit = handoff.read_record(sdir, handoff.LIMIT_FILE)
    if not limit or limit.get("handled"):
        return accounts, ""
    t = acc.now()
    handoff.write_record(sdir, handoff.LIMIT_FILE, {**limit, "handled": acc.utc_text(t)})
    label = str(limit.get("account") or "")
    if label not in [a.label for a in accounts]:
        _say(f"A usage limit was recorded for {label or 'a session with no account label'}.")
        return accounts, label

    def num(key: str) -> int:
        value = limit.get(key)
        return value if isinstance(value, int) else 0

    hit = min(_epoch(str(limit.get("at") or "")) or t, t)
    weekly = num("seven_day_pct")
    if num("five_hour_pct") < acc.DEFAULT_THRESHOLD_PCT <= weekly:
        # The five-hour window was nowhere near its end, so the week is what ran out; the last
        # reading before the hit is often 98 or 99 after rounding.
        weekly = 100
    # The rest counts from the hit, not from now: a record nobody acted on for a day must not
    # bench an account whose window reset long ago.
    until = acc.hibernate_until(hit, num("five_hour_resets_at"), weekly, num("seven_day_resets_at"))
    if until <= t:
        return accounts, ""  # long over: the account is no longer the one that ran out
    accounts = acc.set_hibernation(acc.load_accounts(directory), label, until)
    acc.save_accounts(directory, accounts)
    acc.log_switch(directory, f"{acc.utc_text(t)} limit account={label} rest-until={_when(until)}")
    _say(f"{label} reached its usage limit; it rests until {_when(until)}.")
    return accounts, label


Check = Callable[[acc.Account], acc.AuthVerdict] | None


def _target(
    label: str,
    strategy: str,
    threshold: int,
    directory: str,
    accounts: tuple[acc.Account, ...],
    current: str,
    check: Check,
) -> tuple[tuple[acc.Account, ...], acc.Account]:
    """The account that takes the session: the one named, or the next by the strategy, skipping
    (and taking out of the rotation) any that does not answer as signed in. ``check`` is None when
    the sign-in is checked later, where the directory is bound (the grid's second step)."""
    t = acc.now()
    if label:
        a = _find(accounts, label)
        if not a.enabled:
            raise Refusal(f"{a.label} is out of the rotation; bee account enable {a.label}")
        if a.hibernate_until > t:
            _say(f"Note: {a.label} rests until {_when(a.hibernate_until)}; using it as asked.")
        verdict = check(a) if check else acc.AuthVerdict(ok=True, reason="checked later")
        if not verdict.ok:
            raise Refusal(f"{a.label}: {verdict.reason}. bee account login {a.label}")
        return accounts, a
    usage = acc.load_usage(directory, accounts)
    tried: list[str] = []
    while True:
        chosen = acc.choose_next(accounts, current, t, strategy, usage, threshold)
        if not chosen:
            why = f" ({', '.join(tried)} not signed in, now out of the rotation)" if tried else ""
            raise Refusal(
                "no other account can take over: every one is off, resting or near its limit"
                f"{why}. bee account list"
            )
        a = _find(accounts, chosen)
        verdict = check(a) if check else acc.AuthVerdict(ok=True, reason="checked later")
        if verdict.ok:
            return accounts, a
        accounts = _quarantine(directory, accounts, chosen, verdict.reason)
        tried.append(chosen)


def _quarantine(
    directory: str, accounts: tuple[acc.Account, ...], label: str, reason: str
) -> tuple[acc.Account, ...]:
    """claude-swap's quarantine, by the one read the rule allows: an account that does not answer
    as signed in leaves the rotation and is reported; nothing is repaired or signed out."""
    # Re-read just before the write: another bee may have changed the list while the sign-in was
    # being asked about.
    accounts = acc.set_enabled(acc.load_accounts(directory), label, False)
    acc.save_accounts(directory, accounts)
    acc.log_switch(directory, f"{acc.utc_text(acc.now())} quarantine account={label} {reason}")
    _say(f"{label}: {reason}; taken out of the rotation (bee account login {label}).")
    return accounts


@dataclass(frozen=True)
class _Plan:
    """Who takes the next session and how: ``run`` (no hand-off), ``file`` (hydrate from the
    hand-off file) or ``fork`` (resume the last transcript under the new account, ``owner_dir``
    being the directory of the account that wrote it)."""

    target: acc.Account
    mode: str
    previous: str
    transcript: str
    prompt: str
    session_id: str
    owner_dir: str = ""


def _project_credential_settings(project: str) -> list[str]:
    """Where the project's own Claude Code settings would take a session off the person's own
    subscription. Names only; a value is never read out or printed."""
    found: list[str] = []
    for name in ("settings.json", "settings.local.json"):
        try:
            with open(os.path.join(project, ".claude", name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        found += [f".claude/{name}: {k}" for k in _PROJECT_CREDENTIAL_KEYS if k in data]
        env = data.get("env")
        if isinstance(env, dict):
            found += [f".claude/{name}: env {k}" for k in acc.CREDENTIAL_VARS if k in env]
    return found


def _fork_source(session: dict[str, object], accounts: tuple[acc.Account, ...]) -> tuple[str, str]:
    """For continue --fork: the last session's transcript as this machine sees it, and the
    directory of the account that wrote it. Anything but that account's own session file (a
    sign-in file, a key under $HOME) is refused, never bound or resumed."""
    instead = "continue from the hand-off file instead (bee continue without --fork)"
    raw = str(session.get("transcript_path") or "")
    if not raw:
        raise Refusal(f"the last session's transcript was not recorded; {instead}")
    sid = str(session.get("session_id") or "")
    owner = next((a for a in accounts if a.label == session.get("account")), None)
    if owner is None or not _SESSION_ID.fullmatch(sid):
        raise Refusal(
            f"the last session's record names none of your accounts or no session; {instead}"
        )
    name = f"{sid}.jsonl"
    stray = f"the recorded transcript is not a session file of {owner.label}; {instead}"
    if raw.startswith("/claude/"):
        # Recorded inside the image, where the account's directory is bound at /claude.
        parts = raw.split("/")
        if (
            owner.path is None
            or len(parts) != 5
            or parts[2] != "projects"
            or parts[3] in ("", ".", "..")
            or parts[4] != name
        ):
            raise Refusal(stray)
        return acc.host_transcript(raw, owner.path), owner.path
    home = owner.path or os.path.expanduser("~/.claude")
    real = os.path.realpath(raw)
    folder = os.path.normcase(os.path.realpath(os.path.join(home, "projects")))
    if (
        os.path.normcase(os.path.basename(real)) != os.path.normcase(name)
        or os.path.normcase(os.path.dirname(os.path.dirname(real))) != folder
    ):
        raise Refusal(stray)
    return real, home


def _plan(args: argparse.Namespace, project: str, hydrate: bool, fork: bool, check: Check) -> _Plan:
    directory, accounts = _registry()
    if not accounts:
        raise Refusal("no accounts yet; bee account add main")
    found = _project_credential_settings(project)
    if found:
        raise Refusal(
            "this project's Claude Code settings would run the session on a key or a cloud "
            f"provider instead of your own subscription ({'; '.join(found)}); remove them first"
        )
    accounts, limited = _absorb_limit(project, directory, accounts)
    sdir = handoff.refresh_state(project) if hydrate or fork else handoff.state_dir(project)
    session = handoff.read_record(sdir, handoff.SESSION_FILE)
    shell, last = os.environ.get("BEE_ACCOUNT") or "", str(session.get("account") or "")
    # continue leaves the account the last session ran on; run starts on the shell's own choice.
    current = limited or ((last or shell) if hydrate or fork else (shell or last))
    label = args.label
    if not hydrate and not fork and not label:
        # bee run with no label: the current account if it may run, else the first that may.
        mine = [a for a in accounts if a.label == current and acc.eligible(a, acc.now())]
        label = mine[0].label if mine else ""
    away = current if hydrate or fork else ""
    accounts, target = _target(
        label, args.strategy, args.threshold, directory, accounts, away, check
    )
    transcript, owner_dir, prompt, mode = "", "", "", "run"
    if fork:
        transcript, owner_dir = _fork_source(session, accounts)
        prompt = f"Continue where you stopped; this session now runs as {target.label}."
        mode = "fork"
    elif hydrate:
        report = handoff.context_report(sdir)
        if report is None:
            _say("No hand-off file: the new session starts from the git state alone.")
        elif not report.ok:
            _say(f"The hand-off file is incomplete (missing {list(report.missing)}).")
        prompt = _hydrate_arg(project, sdir)
        mode = "file"
    acc.log_switch(
        directory,
        f"{acc.utc_text(acc.now())} {mode} from={current or '-'} to={target.label} "
        f"project={project} previous-session={session.get('session_id') or '-'}",
    )
    return _Plan(
        target, mode, current, transcript, prompt, str(session.get("session_id") or ""), owner_dir
    )


def _launch(argv: list[str], env: dict[str, str]) -> int:
    """Run Claude Code in the foreground and give back its exit code. Ctrl-C belongs to Claude
    Code while it runs, so this process ignores it."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return subprocess.run(argv, env=env, check=False).returncode
    finally:
        signal.signal(signal.SIGINT, previous)


def _session_env(
    target: acc.Account, gate: bool, hydrate: bool, threshold: int = acc.DEFAULT_THRESHOLD_PCT
) -> dict[str, str]:
    env = acc.clean_child_env(dict(os.environ))
    for name, value in acc.env_pairs(target):
        if value is not None:
            env[name] = value
    env["BEE_THRESHOLD"] = str(threshold)  # the status line and the Stop gate use the same one
    if gate:
        env["BEETHOVEN_HANDOFF_GATE"] = "1"
    if hydrate:
        env["BEETHOVEN_HANDOFF"] = "1"
    return env


def _hydrate_arg(project: str, sdir: str) -> str:
    path = os.path.join(sdir, handoff.HYDRATE_FILE)
    rel = os.path.relpath(path, project) if path.startswith(project + os.sep) else path
    # One plain line: on Windows the npm shim is a .cmd file, and cmd.exe would read newlines or
    # its metacharacters in an argument as its own.
    return f"Read {rel.replace(os.sep, '/')} and follow it."


def _offer_next(project: str, started: float, current: str) -> bool:
    """After Claude Code exits: if this session hit a usage limit, offer to go on with the next
    account. Only at a terminal; a script gets the exit code and decides itself."""
    sdir = handoff.state_dir(project)
    limit = handoff.read_record(sdir, handoff.LIMIT_FILE)
    path = os.path.join(sdir, handoff.LIMIT_FILE)
    if not limit or limit.get("handled") or not os.path.exists(path):
        return False
    if os.path.getmtime(path) < started or not sys.stdin.isatty():
        return False
    who = limit.get("account") or current or "this account"
    answer = input(f"{who} reached its usage limit. Continue on the next account? [Y/n] ")
    return answer.strip().lower() in ("", "y", "yes")


def _run_loop(args: argparse.Namespace, hydrate_first: bool) -> int:
    _ensure_not_nested()
    _ensure_not_login_node()
    claude = _claude()
    project = _project(args.project)
    hydrate = hydrate_first
    fork = bool(getattr(args, "fork", False))
    while True:
        plan = _plan(args, project, hydrate, fork, lambda a: acc.run_status(a, claude)[0])
        argv = [claude]
        if plan.mode == "fork":
            if not os.path.isfile(plan.transcript):
                raise Refusal(
                    "the last session's transcript is not on this machine; continue from the "
                    "hand-off file instead (bee continue without --fork)"
                )
            argv += ["--resume", plan.transcript, "--fork-session"]
        if plan.prompt:
            argv.append(plan.prompt)
        argv += list(args.claude_args or [])
        _say(f"Starting Claude Code as {plan.target.label} ({plan.mode}) in {project}")
        started = float(acc.now()) - 1
        env = _session_env(plan.target, not args.no_gate, plan.mode == "file", args.threshold)
        rc = _launch(argv, env)
        if args.once or not _offer_next(project, started, plan.target.label):
            return rc
        args.label = ""
        hydrate, fork = True, False


def _cmd_run(args: argparse.Namespace) -> int:
    return _run_loop(args, hydrate_first=False)


def _cmd_continue(args: argparse.Namespace) -> int:
    return _run_loop(args, hydrate_first=not args.fork)


def _cmd_plan(args: argparse.Namespace) -> int:
    """The grid's first step (bee-grid.sh): choose the account and the mode inside the image,
    where no sign-in directory is bound, and print one tab-separated line for the script:
    label, host directory, mode, host transcript (fork), prompt, host directory of the account
    that wrote the transcript (fork). Notes go to stderr, so stdout is that one line."""
    project = _project(args.project)
    hydrate = args.mode == "continue" and not args.fork
    out = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        plan = _plan(args, project, hydrate, args.fork, None)
    fields = (
        plan.target.label,
        plan.target.path or "",
        plan.mode,
        plan.transcript,
        plan.prompt,
        plan.owner_dir,
    )
    out.write("\t".join(f.replace("\t", " ").replace("\n", " ") for f in fields) + "\n")
    return 0


# --- bee handoff ---------------------------------------------------------------------------------


def _print_report(report: handoff.ContextReport | None, sdir: str) -> int:
    path = os.path.join(sdir, handoff.CONTEXT_FILE)
    if report is None:
        _say(f"No hand-off file at {path}. Write one with /handoff in a session.")
        return 1
    _say(handoff.describe_context(report, path, ""))
    for r in report.repairs:
        _say(f"  repaired: {r}")
    for w in report.warnings:
        _say(f"  warning: {w}")
    return 0 if report.ok else 1


def _cmd_check(args: argparse.Namespace) -> int:
    sdir = handoff.refresh_state(_project(args.project))
    return _print_report(handoff.context_report(sdir), sdir)


def _cmd_prompt(args: argparse.Namespace) -> int:
    sdir = handoff.state_dir(_project(args.project))
    sys.stdout.write(handoff.extraction_prompt(os.path.join(sdir, handoff.CONTEXT_FILE), args.web))
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    if args.file == "-":
        text = sys.stdin.read()
    else:
        with open(args.file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    sdir = handoff.state_dir(_project(args.project))
    report, hits = handoff.install_context(sdir, text)
    if hits:
        _say(f"{hits} secret-shaped strings were replaced with [redacted].")
    handoff.refresh_state(_project(args.project))
    return _print_report(report, sdir)


def _cmd_where(args: argparse.Namespace) -> int:
    _say(handoff.state_dir(_project(args.project)))
    return 0


def _cmd_log(args: argparse.Namespace) -> int:
    sdir = handoff.state_dir(_project(args.project))
    for path in (os.path.join(sdir, handoff.LOG_FILE), os.path.join(acc.registry_dir(),
                                                                    "switches.log")):  # fmt: skip
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except FileNotFoundError:
            continue
        _say(f"== {path}")
        for line in lines[-args.lines :]:
            _say(line)
    return 0


# --- bee init ------------------------------------------------------------------------------------


def _hook_command(engine: str, name: str) -> str:
    return f'bash "{engine}/{HOOK_SCRIPT}" {name}'


def _merge_hooks(settings: dict[str, object], engine: str) -> int:
    """Add the hand-off hooks and the ignore hook to a settings object, keeping every existing hook
    and skipping any already there. Returns how many were added."""
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise Refusal("the settings file's hooks entry is not an object; fix it by hand first")
    wanted = [(e, m, _hook_command(engine, n), a) for e, m, n, a in HOOK_EVENTS]
    wanted.append(("PreToolUse", IGNORE_MATCHER, _hook_command(engine, "claudeignore"), False))
    added = 0
    for event, matcher, command, is_async in wanted:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise Refusal(f"the settings file's {event} hooks are not a list; fix it by hand first")
        present = any(
            isinstance(g, dict)
            and any(isinstance(h, dict) and h.get("command") == command for h in g.get("hooks", []))
            for g in groups
        )
        if present:
            continue
        hook: dict[str, object] = {"type": "command", "command": command, "timeout": 30}
        if is_async:
            hook["async"] = True
        group: dict[str, object] = {"hooks": [hook]}
        if matcher:
            group["matcher"] = matcher
        groups.append(group)
        added += 1
    return added


def _cmd_init(args: argparse.Namespace) -> int:
    project = _project(args.project)
    engine = os.path.abspath(args.engine or str(ENGINE_ROOT)).replace("\\", "/")
    if not os.path.isfile(os.path.join(engine, HOOK_SCRIPT)):
        raise Refusal(f"{engine} is not an engine checkout (no {HOOK_SCRIPT}); pass --engine DIR")
    claude_dir = os.path.join(project, ".claude")
    os.makedirs(os.path.join(claude_dir, "commands"), exist_ok=True)
    for name, body in (("handoff.md", COMMAND_HANDOFF), ("hydrate.md", COMMAND_HYDRATE)):
        path = os.path.join(claude_dir, "commands", name)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
            _say(f"wrote {path}")
    ignore = os.path.join(project, claudeignore.IGNORE_FILE)
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(IGNORE_TEMPLATE)
        _say(f"wrote {ignore}")
    settings_path = os.path.join(claude_dir, "settings.local.json")
    settings: dict[str, object] = {}
    if os.path.exists(settings_path):
        with open(settings_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise Refusal(f"{settings_path} is not a JSON object; fix it by hand first")
        settings = loaded
    added = _merge_hooks(settings, engine)
    with open(ignore, encoding="utf-8") as fh:
        rules = claudeignore.deny_rules(claudeignore.compile_rules(fh.read()))
    perms = settings.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise Refusal("the settings file's permissions entry is not an object; fix it by hand")
    deny = perms.setdefault("deny", [])
    if not isinstance(deny, list):
        raise Refusal("the settings file's permissions.deny is not a list; fix it by hand")
    # The sign-in files sit outside the project, so the rules name them absolutely (//) and from
    # home (~/); a bare pattern is relative to the folder Claude Code runs in.
    base = [
        "Read(//**/.credentials.json)",
        "Read(~/.claude/.credentials.json)",
        "Read(~/.beethoven/**/.credentials.json)",
        "Read(**/.credentials.json)",
        "Read(./.env)",
        "Read(./.env.*)",
    ]
    # Rules made from .claudeignore last time and gone from it now are taken back out, so a
    # pattern removed or re-included with ! is readable again.
    sdir = handoff.state_dir(project)
    before = handoff.read_record(sdir, INIT_RECORD).get("deny")
    stale = [r for r in before if r not in rules] if isinstance(before, list) else []
    deny[:] = [r for r in deny if r not in stale]
    new_rules = [r for r in [*base, *rules] if r not in deny]
    deny.extend(new_rules)
    handoff.write_record(sdir, INIT_RECORD, {"deny": list(rules)})
    try:
        with open(os.path.join(claude_dir, "settings.json"), encoding="utf-8") as fh:
            shared_status = "statusLine" in json.load(fh)
    except (OSError, ValueError, TypeError):
        shared_status = False
    has_status = "statusLine" in settings or shared_status
    set_status = not args.no_statusline and (not has_status or args.statusline)
    if set_status:
        settings["statusLine"] = {
            "type": "command",
            "command": _hook_command(engine, "statusline"),
            "padding": 0,
        }
    with open(settings_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")
    _exclude_local(project)
    _say(
        f"{settings_path}: {added} hooks added, {len(new_rules)} deny rules added"
        f"{f', {len(stale)} taken back' if stale else ''}, status line "
        f"{'set' if set_status else 'kept as it was'}. Start with: bee run <label>"
    )
    if set_status:
        _say(
            "A status line of your own in your user settings is replaced by bee's in this "
            "project; bee init --no-statusline keeps yours."
        )
    return 0


def _exclude_local(project: str) -> None:
    """Keep ``settings.local.json`` and the state folder out of git through ``.git/info/exclude``
    (the checkout's own, never the shared ``.gitignore``)."""

    def ask(*words: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", project, "rev-parse", *words],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    # A linked worktree reads info/exclude only from the common folder, and a project in a
    # subfolder needs its patterns anchored there (git before 2.5 has no --git-common-dir).
    common = ask("--git-common-dir")
    git_dir = common if common and not common.startswith("--") else ask("--git-dir")
    prefix = ask("--show-prefix")
    if git_dir is None or prefix is None:
        return
    exclude = os.path.join(project, git_dir, "info", "exclude")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    try:
        with open(exclude, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    wanted = (f"/{prefix}.claude/settings.local.json", f"/{prefix}.claude/state/")
    lines = [x for x in wanted if x not in text.splitlines()]
    if lines:
        with open(exclude, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n# Beethoven (bee init)\n" + "\n".join(lines) + "\n")


# --- the parser ----------------------------------------------------------------------------------


def _threshold(text: str) -> int:
    value = int(text)
    if not acc.DEFAULT_HYSTERESIS_PCT < value <= 100:
        raise argparse.ArgumentTypeError(
            f"a percentage above {acc.DEFAULT_HYSTERESIS_PCT} and at most 100"
        )
    return value


def _hours(text: str) -> float:
    value = float(text)
    if not 0 < value <= 24 * 30:
        raise argparse.ArgumentTypeError("more than 0 and at most 720 hours")
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bee", description="Switch Claude accounts and carry the work across (Beethoven)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("account", help="the accounts and their sign-in directories")
    asub = a.add_subparsers(dest="acmd", required=True)
    x = asub.add_parser("add", help="list an account and sign it in, once")
    x.add_argument("label")
    where = x.add_mutually_exclusive_group()
    where.add_argument("--path", help="an existing directory to use as this account's")
    where.add_argument(
        "--default-location",
        action="store_true",
        help="the sign-in Claude Code uses with CLAUDE_CONFIG_DIR unset (~/.claude)",
    )
    x.add_argument("--no-login", action="store_true", help="list it now, sign in later")
    x.add_argument("--no-create", action="store_true", help=argparse.SUPPRESS)
    x.set_defaults(func=_cmd_add)
    x = asub.add_parser("login", help="sign a listed account in (Claude Code's own prompts)")
    x.add_argument("label")
    x.add_argument("--bound", help=argparse.SUPPRESS)
    x.set_defaults(func=_cmd_login)
    x = asub.add_parser("list", help="accounts, rotation, rest and the last usage reading")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=_cmd_list)
    x = asub.add_parser("status", help="ask claude auth status for one account or all")
    x.add_argument("label", nargs="?")
    x.add_argument("--bound", help=argparse.SUPPRESS)
    x.set_defaults(func=_cmd_status)
    for name, on in (("enable", True), ("disable", False)):
        x = asub.add_parser(name, help=f"{name} an account in the rotation")
        x.add_argument("label")
        x.set_defaults(func=lambda ns, on=on: _cmd_enable(ns, on))
    x = asub.add_parser("remove", help="drop an entry from the list (the directory stays)")
    x.add_argument("label")
    x.set_defaults(func=_cmd_remove)
    x = asub.add_parser("env", help="shell lines that switch this shell to an account")
    x.add_argument("label")
    x.add_argument("--shell", choices=["posix", "powershell"], default="posix")
    x.set_defaults(func=_cmd_env)
    x = asub.add_parser("path", help="print an account's directory")
    x.add_argument("label")
    x.set_defaults(func=_cmd_path)
    x = asub.add_parser("rows", help=argparse.SUPPRESS)
    x.set_defaults(func=_cmd_rows)
    x = asub.add_parser("next", help="print the account that would take over next")
    x.add_argument("--from", dest="from_label", default="")
    x.add_argument("--strategy", choices=["order", "best", "consume-first"], default="order")
    x.add_argument("--threshold", type=_threshold, default=acc.DEFAULT_THRESHOLD_PCT)
    x.set_defaults(func=_cmd_next)
    x = asub.add_parser("spent", help="rest an account until its window resets")
    x.add_argument("label")
    x.add_argument("--hours", type=_hours)
    x.set_defaults(func=_cmd_spent)
    x = asub.add_parser("rested", help="wake a resting account")
    x.add_argument("label")
    x.set_defaults(func=_cmd_rested)
    for name, pin in (("pin", True), ("unpin", False)):
        x = asub.add_parser(name, help=f"{name} a project to an account with direnv (.envrc)")
        if pin:
            x.add_argument("label")
        x.add_argument("--project")
        x.set_defaults(func=lambda ns, pin=pin: _cmd_pin(ns, pin))

    for name, func, text in (
        ("run", _cmd_run, "start Claude Code on an account with the hand-off hooks on"),
        ("continue", _cmd_continue, "hand the work to the next account (or the one named)"),
    ):
        x = sub.add_parser(name, help=text)
        x.add_argument("label", nargs="?", default="")
        x.add_argument("--project")
        x.add_argument("--strategy", choices=["order", "best", "consume-first"], default="order")
        x.add_argument("--threshold", type=_threshold, default=acc.DEFAULT_THRESHOLD_PCT)
        x.add_argument("--no-gate", action="store_true", help="no Stop-hook reminders")
        x.add_argument("--once", action="store_true", help="do not offer the next account")
        if name == "continue":
            x.add_argument(
                "--fork",
                action="store_true",
                help="resume the last session's whole conversation under the new account",
            )
        x.set_defaults(func=func, claude_args=[])

    x = sub.add_parser("plan", help=argparse.SUPPRESS)
    x.add_argument("mode", choices=["run", "continue"])
    x.add_argument("label", nargs="?", default="")
    x.add_argument("--project")
    x.add_argument("--fork", action="store_true")
    x.add_argument("--strategy", choices=["order", "best", "consume-first"], default="order")
    x.add_argument("--threshold", type=_threshold, default=acc.DEFAULT_THRESHOLD_PCT)
    x.set_defaults(func=_cmd_plan)

    h = sub.add_parser("handoff", help="the hand-off file")
    hsub = h.add_subparsers(dest="hcmd", required=True)
    for name, func, text in (
        ("check", _cmd_check, "refresh the git state and report on the hand-off file"),
        ("where", _cmd_where, "print the state folder"),
    ):
        x = hsub.add_parser(name, help=text)
        x.add_argument("--project")
        x.set_defaults(func=func)
    x = hsub.add_parser("prompt", help="print the extraction prompt")
    x.add_argument("--web", action="store_true", help="the form to paste into claude.ai")
    x.add_argument("--project")
    x.set_defaults(func=_cmd_prompt)
    x = hsub.add_parser("import", help="place a hand-off file written elsewhere (- for stdin)")
    x.add_argument("file")
    x.add_argument("--project")
    x.set_defaults(func=_cmd_import)
    x = hsub.add_parser("log", help="the hand-off and switch logs")
    x.add_argument("--project")
    x.add_argument("--lines", type=int, default=20)
    x.set_defaults(func=_cmd_log)

    x = sub.add_parser("init", help="set a project up for hand-offs")
    x.add_argument("--project")
    x.add_argument("--engine", help="the engine checkout whose hooks to use")
    x.add_argument("--statusline", action="store_true", help="replace an existing status line")
    x.add_argument(
        "--no-statusline", action="store_true", help="leave the status line to your own settings"
    )
    x.set_defaults(func=_cmd_init)
    return p


@beartype
@ensure(lambda result: 0 <= result <= 255, "an exit code")
def main(argv: list[str] | None = None) -> int:
    """Run one ``bee`` command; a refusal prints its reason and answers 1."""
    # crosshair: off
    words = list(sys.argv[1:] if argv is None else argv)
    tail: list[str] = []
    # Split here, not in argparse: a REMAINDER after an optional label eats options.
    if "--" in words:
        cut = words.index("--")
        words, tail = words[:cut], words[cut + 1 :]
    args = _parser().parse_args(words)
    if tail and not hasattr(args, "claude_args"):
        print("bee: only run and continue pass arguments on to claude", file=sys.stderr)
        return 2
    if tail:
        args.claude_args = tail
    try:
        return int(args.func(args)) & 0xFF
    except Refusal as why:
        print(f"bee: {why}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"bee: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
