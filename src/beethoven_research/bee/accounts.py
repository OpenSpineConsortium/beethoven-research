"""Accounts: one Claude Code sign-in directory per account, chosen with CLAUDE_CONFIG_DIR (D4).

The first part of greg's state-management guide (Drive, "Claude CLI State Management Guide",
2026-10-04), built as the engine's own tooling so the same code runs on a laptop and in the image.
An account is a directory Claude Code signs in to and keeps its own state in; switching is choosing
which directory the next session names. Claude Code's authentication page documents exactly this
(one ``CLAUDE_CONFIG_DIR`` per account), and on macOS the keychain entry is kept per directory too.

Three changes from the guide (docs/bee/STATE_HANDOFF.md says why, with the evidence):

- The default sign-in is "the variable unset", never the guide's alias that sets it to
  ``~/.claude``. With the variable unset Claude Code keeps ``.claude.json`` at ``~/.claude.json``;
  with it set, inside the directory (measured on 2.1.289), so the alias would strand the default
  account's own settings and history index.
- A status counts as signed in only when it is the directory's own claude.ai sign-in. A token in
  the environment, an API key or a cloud provider outranks the directory in Claude Code's
  authentication order and answers ``loggedIn: true`` for a directory nobody signed in to
  (measured in a cloud session, where ``authMethod`` was ``oauth_token``); an API key would also
  bill usage, which the engine never does (D1, C7).
- Every child process gets an environment stripped of every credential and provider variable, for
  the same reason.

What this module never does: read, copy, move, print or relay anything inside a sign-in directory.
``claude auth status`` is the only question asked of one; ``claude auth login`` runs only when the
person asks for it, at their own terminal, with Claude Code's own prompts; ``claude auth logout``,
``setup-token`` and ``--console`` (API billing) are never run. Removing an account edits the list
and never the directory.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from typing import Literal

from beartype import beartype
from icontract import ensure, require
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CREDENTIAL_VARS",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_HYSTERESIS_PCT",
    "DEFAULT_THRESHOLD_PCT",
    "Account",
    "AuthVerdict",
    "Usage",
    "add_account",
    "choose_next",
    "clean_child_env",
    "eligible",
    "env_pairs",
    "hibernate_until",
    "host_transcript",
    "interpret_status",
    "is_absolute",
    "remove_account",
    "render_env",
    "set_enabled",
    "set_hibernation",
    "should_hand_off",
    "valid_label",
]

MAX_LABEL = 32
_LABEL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")

# claude-swap's number for the hand-off threshold (docs/ACCOUNTS.md, section 8). The cooldown is
# its five minutes; the hysteresis margin is this file's choice: an account handed off at 90 %
# rejoins the rotation only once its window reads below 85 %, which in practice means after reset.
DEFAULT_THRESHOLD_PCT = 90
DEFAULT_COOLDOWN_S = 300
DEFAULT_HYSTERESIS_PCT = 5
# When a limit is hit and Claude Code has not told us the reset, the account rests one whole
# five-hour window from the hit. A weekly limit reads its own reset from the status line.
FALLBACK_REST_S = 5 * 3600

# Every variable that outranks a sign-in directory in Claude Code's authentication order, or routes
# the session to a provider that bills (code.claude.com/docs, authentication and env-vars pages,
# read 2026-10-04). A child process never inherits one: the directory decides, and only a
# subscription is ever used.
CREDENTIAL_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
    "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_WORKSPACE_ID",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AWS_API_KEY", "ANTHROPIC_AWS_BASE_URL", "ANTHROPIC_AWS_WORKSPACE_ID",
    "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_BEDROCK_REGION_PREFIX", "ANTHROPIC_BEDROCK_SERVICE_TIER",
    "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID",
    "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN", "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "AWS_BEARER_TOKEN_BEDROCK", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
)  # fmt: skip
# Set fresh for the child from the chosen account, never inherited from the parent's shell.
_ACCOUNT_VARS: tuple[str, ...] = ("CLAUDE_CONFIG_DIR", "BEE_ACCOUNT")

# Plain strings with a precondition rather than Literal types: CrossHair 0.0.111 cannot build a
# symbolic argument for a Literal alias and stops the whole gate (seen 2026-10-04).
STRATEGIES: tuple[str, ...] = ("order", "best", "consume-first")
SHELLS: tuple[str, ...] = ("posix", "powershell")


@dataclass(frozen=True)
class Account:
    """One entry of ``accounts.json``, past the boundary. ``path`` None is Claude Code's default
    place (``CLAUDE_CONFIG_DIR`` unset); ``hibernate_until`` is epoch seconds, 0 when awake."""

    label: str
    path: str | None
    enabled: bool = True
    order: int = 0
    hibernate_until: int = 0


@dataclass(frozen=True)
class Usage:
    """One account's window, as Claude Code last handed it to the status line (epoch seconds,
    whole percent; a reset of 0 means the window was absent from what Claude Code sent)."""

    label: str
    five_hour_pct: int = 0
    five_hour_resets_at: int = 0
    seven_day_pct: int = 0
    seven_day_resets_at: int = 0


@dataclass(frozen=True)
class AuthVerdict:
    """Whether a directory answered as its own claude.ai sign-in, and the plain-words reason."""

    ok: bool
    reason: str


# --- pure logic: labels and paths -----------------------------------------------------------------


@beartype
@ensure(
    lambda result, label: not result or (1 <= len(label) <= MAX_LABEL and label[0] != "-"),
    "a valid label is 1-32 characters and does not start with a hyphen",
)
def valid_label(label: str) -> bool:
    """Lower-case letters, digits and hyphens, at most 32 characters, as docs/ACCOUNTS.md says."""
    return (
        1 <= len(label) <= MAX_LABEL and label[0] != "-" and all(c in _LABEL_CHARS for c in label)
    )


@beartype
@ensure(
    lambda result, path: not result or path[:1] in ("/", "\\") or path[1:3] in (":\\", ":/"),
    "absolute means rooted: POSIX, UNC or a Windows drive",
)
def is_absolute(path: str) -> bool:
    """Whether ``path`` is absolute on either family of systems, without asking the filesystem."""
    if path[:1] in ("/", "\\"):
        return True
    return len(path) >= 3 and path[0].isalpha() and path[1:3] in (":\\", ":/")


def _labels(accounts: tuple[Account, ...]) -> tuple[str, ...]:
    return tuple(a.label for a in accounts)


def _paths(accounts: tuple[Account, ...]) -> tuple[str | None, ...]:
    return tuple(a.path for a in accounts)


# --- pure logic: editing the list -----------------------------------------------------------------


@beartype
@require(lambda label: valid_label(label), "the label is lower-case letters, digits and hyphens")
@require(lambda accounts, label: label not in _labels(accounts), "a label names one account")
@require(lambda path: path is None or is_absolute(path), "a directory is given absolute")
@require(
    lambda accounts, path: path not in _paths(accounts),
    "one directory is one account: two labels on one sign-in would make a switch a no-op",
)
@ensure(
    lambda result, accounts, label: (
        result[:-1] == accounts and result[-1].label == label and result[-1].enabled
    ),
    "the new account is appended, enabled, and nothing else changes",
)
@ensure(
    lambda result: all(a.order < result[-1].order for a in result[:-1]),
    "the new account comes last in the rotation",
)
def add_account(accounts: tuple[Account, ...], label: str, path: str | None) -> tuple[Account, ...]:
    """Append an account to the rotation; the directory itself is the sign-in sitting's business."""
    # crosshair: per_condition_timeout=4
    nxt = max((a.order for a in accounts), default=-1) + 1
    return (*accounts, Account(label=label, path=path, enabled=True, order=nxt))


@beartype
@require(lambda accounts, label: label in _labels(accounts), "the account exists")
@ensure(lambda result, accounts: _labels(result) == _labels(accounts), "same accounts, same order")
@ensure(
    lambda result, accounts, label, enabled: all(
        (new.enabled == enabled) if new.label == label else (new == old)
        for new, old in zip(result, accounts, strict=True)
    ),
    "only the named account changes, and only its switch",
)
def set_enabled(accounts: tuple[Account, ...], label: str, enabled: bool) -> tuple[Account, ...]:
    """Take an account out of the rotation or put it back, without signing anything out."""
    # crosshair: per_condition_timeout=4
    return tuple(replace(a, enabled=enabled) if a.label == label else a for a in accounts)


@beartype
@require(lambda accounts, label: label in _labels(accounts), "the account exists")
@ensure(
    lambda result, accounts, label: result == tuple(a for a in accounts if a.label != label),
    "exactly the named entry leaves the list; the directory is never touched",
)
def remove_account(accounts: tuple[Account, ...], label: str) -> tuple[Account, ...]:
    """Drop an entry from the list. Nothing of ours deletes a sign-in directory, ever."""
    # crosshair: per_condition_timeout=4
    return tuple(a for a in accounts if a.label != label)


@beartype
@require(lambda accounts, label: label in _labels(accounts), "the account exists")
@require(lambda until: until >= 0, "a time, or 0 to wake the account")
@ensure(
    lambda result, accounts, label, until: all(
        (new.hibernate_until == until and replace(new, hibernate_until=old.hibernate_until) == old)
        if new.label == label
        else new == old
        for new, old in zip(result, accounts, strict=True)
    ),
    "only the named account's rest changes",
)
def set_hibernation(accounts: tuple[Account, ...], label: str, until: int) -> tuple[Account, ...]:
    """Rest a spent account until its window resets (``until`` epoch seconds), or wake it (0)."""
    # crosshair: per_condition_timeout=4
    return tuple(replace(a, hibernate_until=until) if a.label == label else a for a in accounts)


# --- pure logic: who goes next --------------------------------------------------------------------


@beartype
@ensure(
    lambda result, account, now: result == (account.enabled and account.hibernate_until <= now),
    "awake and enabled, nothing else",
)
def eligible(account: Account, now: int) -> bool:
    """Whether an account may take the next session at ``now``."""
    return account.enabled and account.hibernate_until <= now


def _window(usage: tuple[Usage, ...], label: str, now: int) -> tuple[int, int]:
    """The account's live 5-hour reading as (percent, reset), (0, 0) when none is live."""
    for u in usage:
        if u.label == label and u.five_hour_resets_at > now:
            return (u.five_hour_pct, u.five_hour_resets_at)
    return (0, 0)


def _weekly_spent(usage: tuple[Usage, ...], label: str, now: int) -> bool:
    return any(
        u.label == label and u.seven_day_resets_at > now and u.seven_day_pct >= 100 for u in usage
    )


def _candidates(
    accounts: tuple[Account, ...],
    current: str,
    now: int,
    usage: tuple[Usage, ...],
    threshold: int,
    hysteresis: int,
) -> tuple[Account, ...]:
    """Accounts that may take over, in rotation order starting after ``current``."""
    ordered = sorted(accounts, key=lambda a: a.order)
    labels = [a.label for a in ordered]
    start = labels.index(current) + 1 if current in labels else 0
    cyclic = ordered[start:] + ordered[:start]
    return tuple(
        a
        for a in cyclic
        if a.label != current
        and eligible(a, now)
        and _window(usage, a.label, now)[0] < threshold - hysteresis
        and not _weekly_spent(usage, a.label, now)
    )


@beartype
@require(lambda strategy: strategy in STRATEGIES, "a known strategy")
@require(lambda threshold: 1 <= threshold <= 100, "a percentage")
@require(lambda hysteresis, threshold: 0 <= hysteresis < threshold, "a margin below the threshold")
@ensure(
    lambda result, accounts, current, now, usage, threshold, hysteresis: (
        (result == "")
        == (len(_candidates(accounts, current, now, usage, threshold, hysteresis)) == 0)
    ),
    "an account is named exactly when one can take over",
)
@ensure(
    lambda result, accounts, current, now, usage, threshold, hysteresis: (
        result == ""
        or result in _labels(_candidates(accounts, current, now, usage, threshold, hysteresis))
    ),
    "the one named is awake, enabled, not the current one, and below the threshold less the margin",
)
def choose_next(
    accounts: tuple[Account, ...],
    current: str,
    now: int,
    strategy: str,
    usage: tuple[Usage, ...] = (),
    threshold: int = DEFAULT_THRESHOLD_PCT,
    hysteresis: int = DEFAULT_HYSTERESIS_PCT,
) -> str:
    """The label of the account that takes the next session, or "" when none can.

    ``order`` is the rotation; ``best`` the lowest live 5-hour reading (claude-swap's "best"); and
    ``consume-first`` the window that resets soonest, so quota already running is not wasted
    (claude-swap's "consume first"). Ties keep rotation order. An account with no live reading
    counts as a fresh window.
    """
    # crosshair: per_condition_timeout=6
    pool = _candidates(accounts, current, now, usage, threshold, hysteresis)
    if not pool:
        return ""
    if strategy == "best":
        return min(enumerate(pool), key=lambda p: (_window(usage, p[1].label, now)[0], p[0]))[
            1
        ].label
    if strategy == "consume-first":

        def soonest(p: tuple[int, Account]) -> tuple[int, int, int]:
            reset = _window(usage, p[1].label, now)[1]
            return (0 if reset else 1, reset, p[0])

        return min(enumerate(pool), key=soonest)[1].label
    return pool[0].label


@beartype
@require(lambda threshold: 1 <= threshold <= 100, "a percentage")
@require(lambda cooldown_s: cooldown_s >= 0, "a duration")
@ensure(lambda result, limit_hit: result or not limit_hit, "a hit limit always hands off")
@ensure(
    lambda result, used_pct, threshold, limit_hit: not result or limit_hit or used_pct >= threshold,
    "below the threshold, only a hit limit hands off",
)
@ensure(
    lambda result, since_switch_s, cooldown_s, limit_hit: (
        not result or limit_hit or since_switch_s >= cooldown_s
    ),
    "inside the cooldown, only a hit limit hands off (no flip-flop near the threshold)",
)
def should_hand_off(
    used_pct: int,
    threshold: int,
    since_switch_s: int,
    cooldown_s: int,
    limit_hit: bool,
) -> bool:
    """Whether the account in use should hand over now: at the limit, or at the threshold once the
    cooldown since the last switch has passed."""
    return limit_hit or (used_pct >= threshold and since_switch_s >= cooldown_s)


@beartype
@require(lambda now: now >= 0, "epoch seconds")
@ensure(lambda result, now: result > now, "a rest always ends in the future")
@ensure(
    lambda result, now, resets_at, weekly_pct, weekly_resets_at: (
        result == weekly_resets_at
        if weekly_pct >= 100 and weekly_resets_at > now
        else (resets_at <= now or result == resets_at)
    ),
    "a spent week rests until the week resets; otherwise a known five-hour reset ends the rest",
)
def hibernate_until(
    now: int, resets_at: int, weekly_pct: int = 0, weekly_resets_at: int = 0
) -> int:
    """When a spent account wakes: the weekly reset when the 7-day window is spent, else the
    5-hour window's own reset when Claude Code gave one, else one whole 5-hour window from now."""
    if weekly_pct >= 100 and weekly_resets_at > now:
        return weekly_resets_at
    return resets_at if resets_at > now else now + FALLBACK_REST_S


# --- pure logic: the environment of a session -----------------------------------------------------


@beartype
@ensure(
    lambda result, account: (
        result == (("CLAUDE_CONFIG_DIR", account.path), ("BEE_ACCOUNT", account.label))
    ),
    "the directory (None: unset, Claude Code's default place) and the label, nothing else",
)
def env_pairs(account: Account) -> tuple[tuple[str, str | None], ...]:
    """The two variables that make a session this account's: name and value, None to unset."""
    return (("CLAUDE_CONFIG_DIR", account.path), ("BEE_ACCOUNT", account.label))


def _quote_posix(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _quote_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@beartype
@require(
    lambda pairs: all(name.replace("_", "").isalnum() and name.isascii() for name, _ in pairs),
    "variable names are plain identifiers",
)
@require(
    lambda pairs: all(v is None or ("\n" not in v and "\r" not in v) for _, v in pairs),
    "one value per line",
)
@require(lambda shell: shell in SHELLS, "a known shell")
@ensure(lambda result, pairs: result.count("\n") == len(pairs), "one line per variable")
def render_env(pairs: tuple[tuple[str, str | None], ...], shell: str) -> str:
    """Lines a shell evaluates to switch: ``eval "$(bee account env x)"`` or PowerShell's
    ``bee account env x --shell powershell | Invoke-Expression``. Values are single-quoted."""
    # crosshair: per_condition_timeout=4
    lines: list[str] = []
    for name, value in pairs:
        if shell == "posix":
            lines.append(
                f"unset {name}" if value is None else f"export {name}={_quote_posix(value)}"
            )
        elif value is None:
            lines.append(f"Remove-Item Env:{name} -ErrorAction SilentlyContinue")
        else:
            lines.append(f"$env:{name} = {_quote_powershell(value)}")
    return "".join(line + "\n" for line in lines)


@beartype
@ensure(
    lambda result: not any(k in result for k in CREDENTIAL_VARS + _ACCOUNT_VARS),
    "no credential, provider or account variable survives into the child",
)
@ensure(
    lambda result, environ: all(environ.get(k) == v for k, v in result.items()),
    "nothing is added or changed, only removed",
)
def clean_child_env(environ: dict[str, str]) -> dict[str, str]:
    """The parent's environment less everything that would outrank or override the directory."""
    # crosshair: per_condition_timeout=4
    drop = set(CREDENTIAL_VARS) | set(_ACCOUNT_VARS)
    return {k: v for k, v in environ.items() if k not in drop}


# --- pure logic: reading what Claude Code answered ------------------------------------------------

_METHOD_REASONS = {
    "api_key": "an API key answered, which bills per use; Beethoven uses only a Claude plan",
    "api_key_helper": "an API key helper answered, which bills per use; Beethoven uses only a "
    "Claude plan",
    "oauth_token": "a token from the environment answered, not this directory's own sign-in",
    "third_party": "a cloud provider answered, not a Claude plan",
    "none": "not signed in",
}


@beartype
@ensure(
    lambda result, payload: (
        not result.ok
        or (payload.get("loggedIn") is True and payload.get("authMethod") == "claude.ai")
    ),
    "only the directory's own claude.ai sign-in counts",
)
@ensure(
    lambda result, payload, expected_dir: (
        not result.ok
        or expected_dir is None
        or payload.get("configDirectory") in (None, expected_dir)
    ),
    "a status for another directory never counts",
)
@ensure(lambda result: result.reason != "", "every answer says why")
def interpret_status(payload: dict[str, object], expected_dir: str | None) -> AuthVerdict:
    """Judge ``claude auth status --json`` for one directory (``expected_dir`` already resolved by
    the caller, or None to skip the comparison)."""
    # crosshair: per_condition_timeout=4
    if payload.get("loggedIn") is not True:
        return AuthVerdict(ok=False, reason="not signed in")
    method = payload.get("authMethod")
    if method != "claude.ai":
        reason = _METHOD_REASONS.get(str(method), f"signed in by an unrecognised method ({method})")
        return AuthVerdict(ok=False, reason=reason)
    provider = payload.get("apiProvider")
    if provider not in (None, "firstParty"):
        return AuthVerdict(ok=False, reason=f"routed to {provider}, not a Claude plan")
    seen = payload.get("configDirectory")
    if expected_dir is not None and seen is not None and seen != expected_dir:
        return AuthVerdict(ok=False, reason=f"Claude Code answered for another directory ({seen})")
    if seen is None:
        return AuthVerdict(
            ok=True, reason="signed in (this Claude Code does not name the directory)"
        )
    return AuthVerdict(ok=True, reason="signed in")


@beartype
@require(lambda container_path: container_path.startswith("/claude/"), "a path under the bind")
@require(
    lambda container_path: ".." not in container_path.split("/"), "no parent steps out of the bind"
)
@require(lambda host_dir: is_absolute(host_dir), "the account's directory on the host, absolute")
@ensure(
    lambda result, host_dir, container_path: (
        result == host_dir.rstrip("/\\") + container_path[len("/claude") :]
    ),
    "the same file, seen from the host",
)
def host_transcript(container_path: str, host_dir: str) -> str:
    """Where a transcript a containerised session recorded (under ``/claude``) lies on the host,
    so the next job can bind that one file read-only."""
    # crosshair: per_condition_timeout=4
    return host_dir.rstrip("/\\") + container_path[len("/claude") :]


# --- the boundary: accounts.json ------------------------------------------------------------------


class AccountRecord(BaseModel):
    """One account as stored. Labels, paths and the engine's own bookkeeping; nothing from inside
    any sign-in directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    path: str | None
    enabled: bool = True
    order: int = Field(ge=0)
    hibernate_until: str | None = None

    @model_validator(mode="after")
    def _absolute(self) -> AccountRecord:
        if self.path is not None and not is_absolute(self.path):
            raise ValueError(f"account {self.label}: the directory must be absolute")
        if self.hibernate_until is not None:
            _parse_utc(self.hibernate_until)
        return self


class RegistryFile(BaseModel):
    """``accounts.json``: version 1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    accounts: tuple[AccountRecord, ...] = ()

    @model_validator(mode="after")
    def _distinct(self) -> RegistryFile:
        labels = [a.label for a in self.accounts]
        if len(set(labels)) != len(labels):
            raise ValueError("a label appears twice")
        paths = [a.path for a in self.accounts]
        if len(set(paths)) != len(paths):
            raise ValueError("one directory is listed under two labels")
        return self


def _parse_utc(text: str) -> int:
    stamp = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"{text}: a time in the list carries its zone")
    return int(stamp.timestamp())


@beartype
@require(lambda epoch: 0 <= epoch < 253402300800, "a time this side of the year 10000")
@ensure(lambda result: len(result) == 20 and result.endswith("Z"), "YYYY-MM-DDTHH:MM:SSZ")
def utc_text(epoch: int) -> str:
    """Epoch seconds as the list writes them: ISO 8601, UTC, whole seconds."""
    # crosshair: off
    return _dt.datetime.fromtimestamp(epoch, _dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_account(rec: AccountRecord) -> Account:
    until = _parse_utc(rec.hibernate_until) if rec.hibernate_until else 0
    return Account(rec.label, rec.path, rec.enabled, rec.order, until)


def _to_record(acc: Account) -> AccountRecord:
    until = utc_text(acc.hibernate_until) if acc.hibernate_until else None
    return AccountRecord(
        label=acc.label,
        path=acc.path,
        enabled=acc.enabled,
        order=acc.order,
        hibernate_until=until,
    )


# --- input and output: kept at the edges, so the logic above is what the gate proves --------------


@beartype
@ensure(lambda result: is_absolute(result), "an absolute directory")
def beethoven_home(environ: dict[str, str] | None = None) -> str:
    """``$BEETHOVEN_HOME``, else ``~/.beethoven``: the launcher's folder, where sign-ins live."""
    # crosshair: off
    env = dict(os.environ) if environ is None else environ
    return os.path.abspath(env.get("BEETHOVEN_HOME") or os.path.expanduser("~/.beethoven"))


@beartype
@ensure(lambda result: is_absolute(result), "an absolute directory")
def registry_dir(environ: dict[str, str] | None = None) -> str:
    """Where the engine keeps its own files about accounts: ``accounts.json``, the last usage
    readings and the switch log. ``$BEE_REGISTRY_DIR``, else ``<beethoven home>/engine``. Never a
    sign-in directory, and a job can bind it without binding any sign-in."""
    # crosshair: off
    env = dict(os.environ) if environ is None else environ
    return os.path.abspath(
        env.get("BEE_REGISTRY_DIR") or os.path.join(beethoven_home(env), "engine")
    )


@beartype
@require(lambda label: valid_label(label), "a valid label")
@ensure(lambda result: is_absolute(result), "an absolute directory")
def default_account_dir(label: str, environ: dict[str, str] | None = None) -> str:
    """``<beethoven home>/accounts/<label>/claude`` (docs/ACCOUNTS.md, section 2)."""
    # crosshair: off
    return os.path.join(beethoven_home(environ), "accounts", label, "claude")


@beartype
@ensure(lambda result: isinstance(result, tuple), "a tuple of accounts")
def load_accounts(directory: str) -> tuple[Account, ...]:
    """Read ``accounts.json``; an absent file is an empty list. A malformed one raises: a list
    the engine cannot read is not a list it may rewrite."""
    # crosshair: off
    path = os.path.join(directory, "accounts.json")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return ()
    return tuple(_to_account(r) for r in RegistryFile.model_validate_json(raw).accounts)


@beartype
@ensure(lambda result: result is None, "written or raised")
def save_accounts(directory: str, accounts: tuple[Account, ...]) -> None:
    """Write ``accounts.json`` whole and atomically, mode 0600, its folder 0700."""
    # crosshair: off
    data = RegistryFile(accounts=tuple(_to_record(a) for a in accounts))
    _write_private(directory, "accounts.json", data.model_dump_json(indent=2) + "\n")


def _write_private(directory: str, name: str, text: str) -> None:
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if os.name == "posix":
        os.chmod(directory, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, os.path.join(directory, name))
    except BaseException:
        # Our own temp file, made a moment ago in our own folder: the one thing removed here.
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@beartype
@ensure(lambda result: isinstance(result, tuple), "a tuple of readings")
def load_usage(directory: str, accounts: tuple[Account, ...]) -> tuple[Usage, ...]:
    """The last window each account's status line saw (``usage/<label>.json``); unreadable or
    absent files are simply no reading."""
    # crosshair: off
    found: list[Usage] = []
    for a in accounts:
        try:
            with open(os.path.join(directory, "usage", f"{a.label}.json"), encoding="utf-8") as fh:
                raw = json.load(fh)
            found.append(
                Usage(
                    label=a.label,
                    five_hour_pct=int(raw.get("five_hour_pct") or 0),
                    five_hour_resets_at=int(raw.get("five_hour_resets_at") or 0),
                    seven_day_pct=int(raw.get("seven_day_pct") or 0),
                    seven_day_resets_at=int(raw.get("seven_day_resets_at") or 0),
                )
            )
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return tuple(found)


@beartype
@ensure(lambda result: result is None, "appended or raised")
def log_switch(directory: str, line: str) -> None:
    """One line per switch in ``switches.log`` (every switch is logged, D4): labels and times."""
    # crosshair: off
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, "switches.log")
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line.replace("\n", " ") + "\n")
    if os.name == "posix":
        os.chmod(path, 0o600)


@beartype
@ensure(lambda result: result == "" or "login node" in result, "empty, or a refusal that says why")
def login_node_refusal(environ: dict[str, str] | None = None) -> str:
    """Why Claude Code must not start here, or "": on a cluster's login node (a scheduler on the
    path and no job) nothing of ours runs beyond one scheduler call (grid etiquette)."""
    # crosshair: off
    env = dict(os.environ) if environ is None else environ
    if env.get("SLURM_JOB_ID") or env.get("PBS_JOBID") or env.get("LSB_JOBID"):
        return ""
    if env.get("BEE_NOT_A_CLUSTER") == "1":
        return ""
    for tool in ("sbatch", "qsub", "bsub"):
        if shutil.which(tool, path=env.get("PATH")):
            return (
                f"this looks like a cluster login node ({tool} is on the path and no job is "
                "running); Claude Code runs on a compute node, inside a job (bee-grid.sh)"
            )
    return ""


@beartype
@ensure(lambda result: result is None or is_absolute(result), "absent, or an absolute path")
def find_claude(environ: dict[str, str] | None = None) -> str | None:
    """The ``claude`` the person would get in this shell (``claude.exe`` or the npm shim on
    Windows), or None."""
    # crosshair: off
    env = dict(os.environ) if environ is None else environ
    found = shutil.which(env.get("BEE_CLAUDE") or "claude", path=env.get("PATH"))
    return os.path.abspath(found) if found else None


@beartype
@ensure(lambda result: result[1] is None or isinstance(result[1], dict), "a verdict and details")
def run_status(
    account: Account, claude: str, environ: dict[str, str] | None = None
) -> tuple[AuthVerdict, dict[str, str] | None]:
    """Ask ``claude auth status --json`` for one account's directory, in a clean environment.
    The second value carries only the fields worth showing a person (email, plan)."""
    # crosshair: off
    env = clean_child_env(dict(os.environ) if environ is None else environ)
    if account.path is not None:
        env["CLAUDE_CONFIG_DIR"] = account.path
    try:
        done = subprocess.run(
            [claude, "auth", "status", "--json"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AuthVerdict(
            ok=False, reason=f"claude could not be asked ({exc.__class__.__name__})"
        ), None
    try:
        payload = json.loads(done.stdout)
    except ValueError:
        return AuthVerdict(ok=False, reason="claude auth status did not answer in JSON"), None
    if not isinstance(payload, dict):
        return AuthVerdict(ok=False, reason="claude auth status did not answer an object"), None
    # Compare the directory Claude Code answered for with the one we named, both resolved, so a
    # link (accounts/main/claude) or a drive letter's case is not a false mismatch.
    named = account.path if account.path is not None else os.path.expanduser("~/.claude")
    expected = os.path.normcase(os.path.realpath(named))
    seen = payload.get("configDirectory")
    if isinstance(seen, str):
        payload = {**payload, "configDirectory": os.path.normcase(os.path.realpath(seen))}
    verdict = interpret_status(payload, expected)
    shown = {k: str(payload[k]) for k in ("email", "subscriptionType", "orgName") if payload.get(k)}
    return verdict, shown


@beartype
@ensure(lambda result: isinstance(result, int), "claude's exit code")
def run_login(account: Account, claude: str, environ: dict[str, str] | None = None) -> int:
    """``claude auth login`` for one directory, at the person's own terminal: Claude Code prints its
    link and reads the code back itself; nothing passes through us. Never ``--console``."""
    # crosshair: off
    env = clean_child_env(dict(os.environ) if environ is None else environ)
    if account.path is not None:
        env["CLAUDE_CONFIG_DIR"] = account.path
    env["BEE_ACCOUNT"] = account.label
    return subprocess.run([claude, "auth", "login"], env=env, check=False).returncode


@beartype
@require(lambda label: valid_label(label), "a valid label")
@ensure(lambda result: is_absolute(result), "the directory to record")
def prepare_account_dir(label: str, environ: dict[str, str] | None = None) -> str:
    """Make ``accounts/<label>/claude`` (mode 0700) for Claude Code to fill, and return the path
    to record. For ``main`` with the launcher's ``~/.beethoven/claude`` present, the account is
    that directory: a link from ``accounts/main/claude`` where links work, else its own path.
    Nothing under an existing directory is moved, renamed or copied."""
    # crosshair: off
    home = beethoven_home(environ)
    target = default_account_dir(label, environ)
    launcher = os.path.join(home, "claude")
    if label == "main" and os.path.isdir(launcher) and not os.path.lexists(target):
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        try:
            os.symlink(launcher, target, target_is_directory=True)
            return target
        except (OSError, NotImplementedError):
            return launcher  # Windows without the right to make links: name the directory itself
    os.makedirs(target, mode=0o700, exist_ok=True)
    if os.name == "posix" and not os.path.islink(target):
        os.chmod(target, 0o700)
    return target


@beartype
@ensure(lambda result: result > 0, "epoch seconds")
def now() -> int:
    """Epoch seconds now (the one clock the command line reads; the logic above takes it as an
    argument so the gate can prove it for every time)."""
    # crosshair: off
    return int(_dt.datetime.now(_dt.UTC).timestamp())
