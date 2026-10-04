# The state-management guide, as built

greg's ask of 2026-10-04 04:12Z: "read [the Claude CLI State Management Guide] and implement that
protocol so I can switch from one account to another seamlessly, both on my local machine, and the
grid, in a singularity container in the research engine". The guide is greg's Drive document
*Claude CLI State Management Guide* ("Architecting Continuous Project State and Context Lifecycles
in Claude Environments"). This file says, part by part, what `bee` took from it, what it changed and
why, and what it refused. The commands are in [RUNBOOK.md](RUNBOOK.md); the account design they
serve is [../ACCOUNTS.md](../ACCOUNTS.md) (decision D4).

Every fact marked *documented* was read on code.claude.com/docs on 2026-10-04; *measured* means run
in a cloud session with Claude Code 2.1.289 the same day. Beethoven runs Claude Code and is not an
Anthropic product; "Claude" and "Claude Code" are Anthropic's marks, used here to say what it runs.

## 1. What a switch is, in one paragraph

An account is a directory that Claude Code filled when the student signed in to it once
(`~/.beethoven/accounts/<label>/claude`). Switching is choosing which directory the next Claude Code
session gets as `CLAUDE_CONFIG_DIR`, and nothing else: no file is copied, moved or overwritten, and
nothing of ours opens anything inside a sign-in directory except asking `claude auth status`. What
carries the work across is the guide's hand-off file (section 3), written by the session that
stops and read by the session that starts, plus a state file generated from git after every edit
(section 4). The same Python code does this on a laptop and inside the engine's image on a compute
node; on the grid `bee-grid.sh` wraps each step in `apptainer exec`, binding exactly one sign-in
directory per container.

## 2. Multi-account isolation (the guide's first part)

| Guide | Here | Why |
| - | - | - |
| `CLAUDE_CONFIG_DIR` per profile, isolating sign-in, settings and history | **adopted** | *documented*: "each directory has its own settings, session history, and claude.ai login" (authentication page); the credential file and, on macOS, the Keychain entry are keyed to the directory, so two directories are two sign-ins with nothing shared |
| profiles under `~/.config/claude_profiles/<name>` | **changed** to `~/.beethoven/accounts/<label>/claude` | D4 had already fixed the place; `main` is a link to the launcher's `~/.beethoven/claude`, so the worker's sign-in is one of the accounts without moving it |
| `claude-auth-profile <name>`: `mkdir`, export, `claude auth login` | **adopted** as `bee account add <label>` | the same sitting, plus a 0700 directory, a checked label, the list entry, and `claude auth status` read back; `--console` (an API key that bills per use) is never passed, since usage credits are never used |
| `claude-switch <name>`: export `CLAUDE_CONFIG_DIR` in the shell | **adopted** as `bee-use <label>` (`shell/bee.sh`, `shell/bee.ps1`) | it also sets `BEE_ACCOUNT`, which the hooks and the status line use to say which account a session runs on, and unsets every credential variable: an API key or a token left in the shell outranks the directory, and the switch would do nothing |
| `alias claude='env CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR:-$HOME/.claude} claude'` | **refused** | *measured*: with the variable set, Claude Code keeps `.claude.json` inside the directory, while the default account keeps it at `~/.claude.json`. Pointing the default at `~/.claude` therefore strands the default account's global state (onboarding, trust, personal MCP servers) and it looks signed out. `bee` keeps the default account as "variable unset" (`bee account add <label> --default-location`), and `bee-use` unsets it |
| `ls` of the profiles folder as the account list | **changed** to `accounts.json` (label, path, enabled, order, rest-until) | the list must not be read off the disk inside sign-in directories; `bee account list` shows it and `bee account status` asks each directory's `claude auth status` |
| direnv `.envrc` pinning a project to an account | **adopted** as `bee account pin <label>` / `unpin` | a marked block in `.envrc`, so the rest of the file is left as it was; for a pinned project `bee run` with no label uses the pinned account |
| the scope table: "OAuth tokens" in `.claude.json` | **corrected** | the sign-in itself is `.credentials.json` (Linux, Windows) or a Keychain entry (macOS) (*documented*, authentication page); `.claude.json` holds the account's app state. Both live inside the directory once the variable is set, which is all the switch relies on |
| "Enterprise Managed: remote payload via Console" | **extended** | managed settings are also a file (`/etc/claude-code/managed-settings.json` on Linux); the image bakes one, and it now names bee's hooks (section 6) |

What `bee` adds that the guide does not have, from D4: the rotation (`bee continue` picks the next
enabled, signed-in, rested account in the list's order, or by `--strategy best` or
`consume-first` on the usage readings), the 90 % threshold with a 5-minute cooldown and a 5-point
margin so the choice does not flip-flop, rest until the window's own reset after a limit is hit, and
quarantine: an account whose `claude auth status` does not answer "signed in with a claude.ai
subscription in this very directory" is disabled in the list and reported, never repaired.

*Measured* while building it: in a session whose environment carried a sign-in token from outside,
`claude auth status` for an empty directory answered `loggedIn: true` with `authMethod:
"oauth_token"`. `bee` counts an account as signed in only for `authMethod: "claude.ai"` with the
directory's own `configDirectory`, and strips every credential variable (`ANTHROPIC_API_KEY`,
`CLAUDE_CODE_OAUTH_TOKEN` and the rest, `accounts.py` `CREDENTIAL_VARS`) from the environment of
every Claude Code it starts, so a token from elsewhere can never stand in for an account.

## 3. The hand-off file (extraction and hydration)

| Guide | Here | Why |
| - | - | - |
| the six-section schema (`Timestamp`/`Phase`, objective, decisions, files table, milestones, blockers with exact strings, 3 to 5 next actions) | **adopted verbatim** (`handoff.py` `SCHEMA`) | it is the right contract; `bee handoff check` validates a file against it |
| the extraction mega-prompt, pasted as the last message in claude.ai | **adopted** as `bee handoff prompt --web`, with `bee handoff import FILE` to place the answer | nothing of ours runs on claude.ai: greg pastes the prompt and saves the reply himself |
| the same prompt inside Claude Code | **adopted** as the `/handoff` command and `EXTRACT.md` | written by `bee init`; the session writes the file with its own Write tool |
| the file in the project root as `claude_context.md` | **moved** to `.claude/state/claude_context.md` (or `$BEETHOVEN_RUN_ROOT/state/` in a job) | the state folder ignores itself in git, so the hand-off never lands in a commit by accident |
| the hydration prompt (read, validate against the disk, verify blockers, sync CLAUDE.md, execute, do not ask) | **adopted** as `HYDRATE.md` and the `/hydrate` command; `bee continue` starts the next account with "Read .claude/state/HYDRATE.md and follow it." | step 2 tells the session to check with `git status`, `grep -n` and `ls`, never whole-file reads: the guide's own answer to token exhaustion |
| step 4, "append missing decisions to CLAUDE.md" | **narrowed**: under one heading, "Decisions carried by hand-off", committed on its own | instruction files are part of the paper's record (CLAUDE.md), so the change is visible and revertible as one commit |
| "keep the file small" | **enforced**: a warning past 8 KB, and the session-start injection is capped at 9,500 characters | *documented*: a hook's injected context is capped at 10,000 characters |
| (not in the guide) | **added**: secret-shaped strings (keys, tokens, private-key blocks) are replaced with `[redacted]` whenever the file is written or imported, and the count is reported | the file travels between accounts and machines; a secret must never travel with it |
| (not in the guide) | **added**: the Stop gate | with `bee run`/`bee continue` (`BEETHOVEN_HANDOFF_GATE=1`), when the account's 5-hour window reaches the threshold, or when a commit made the hand-off file stale, the Stop hook asks the session once to refresh the file before it ends the turn, so the file is current *before* the limit, not written in a hurry after it |

The other carrier, kept as the fallback: `bee continue --fork` resumes the stopped session's own
transcript under the next account with `--resume <path> --fork-session` (greg's 03:23Z decision,
ACCOUNTS.md section 5). On the grid the transcript is bound read-only into the next container. It
costs a full uncached first turn and is probe M13's to measure; the hand-off file costs a few
thousand tokens and works across machines, claude.ai and Claude Code versions, so it is the default.

## 4. Continuous state (the dual-hook loop)

| Guide | Here | Why |
| - | - | - |
| `PostToolUse` on `Edit\|Write\|Bash` writes `PROJECT_STATE.md` from `git status`, recent commits and the diff summary | **adopted** (`handoff.py post-tool-use`), run `async` | *documented*: `async: true` runs a hook without holding the session; the state is rewritten only when the git fingerprint or the hand-off file changed, so a long run of edits costs one `git` pass each |
| `SessionStart` on `compact\|resume` injects the state with `cat` | **adopted and widened** to `startup\|resume\|compact\|fork\|clear` | on a resume, compaction or fork the state is injected; on a plain start only one line naming the hand-off file, unless the start is a hand-off (`BEETHOVEN_HANDOFF=1`), which injects the state and the instruction to follow `HYDRATE.md` |
| (not in the guide) | **added**: `session.json` | the session hook records the session id, the account, the transcript's path and the commit at start; it is what `--fork` and the engine's lease hand-off (BUILD_PLAN §17.2.9) need, written by the session itself so nothing of ours lists a sign-in directory |
| (not in the guide) | **added**: the usage limit is recorded where it happens | the `StopFailure` hook (*documented*: it fires when a turn ends on an API error, with `error: "rate_limit"` for a usage limit) writes `limit.json` and rings the terminal (`terminalSequence`); `bee run` then offers the next account, and the spent one rests until its reset |
| (not in the guide) | **added**: the status line | *documented*: the status line's input carries `rate_limits.five_hour` and `seven_day` with `used_percentage` and `resets_at` (Pro and Max, after the first response) and `context_window.used_percentage`. `bee`'s status line shows the account, both windows and the context, writes the reading where `bee continue --strategy best` and the Stop gate read it, and says what to do at the two thresholds |

This settles the question BUILD_PLAN left open for an interactive session: how Claude Code reports
the window (Linear BEE-121, BEE-188). The status line runs only in interactive sessions, so a `-p`
step still has no reading (it has `StopFailure`); that half stays not yet measured.

## 5. Compaction, permissions and subagents

| Guide | Here | Why |
| - | - | - |
| the 60 % rule | **adopted** as the status line's prompt at 60 % context ("/compact focus on: ..."); the CLAUDE.md "Compact Instructions" section is left to each project | nothing compacts behind the student's back; the threshold is a hint |
| `/compact focus on: ...` | **adopted**; the `PreCompact` hook refreshes `PROJECT_STATE.md` and re-checks the hand-off file, and the `SessionStart` hook with the `compact` source injects the state after the summary | a `PreCompact` hook cannot add to the summary itself (hooks reference), so the state reaches the next turn through `SessionStart` |
| `/autocompact 100k` | **changed** to the `--autocompact` flag for one launch (`bee run -- --autocompact 100k`) or `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` in a job | *documented* (model configuration page): `/autocompact` saves the window to user settings, which with `CLAUDE_CONFIG_DIR` set is a file inside the sign-in directory, so it changes every later session of that account; the flag overrides the saved setting for one launch without changing it; the variable takes a plain integer only (`100k` reads as 100 and clamps to the 100,000 minimum) |
| "/compact is undocumented" | corrected | it is documented (commands page) |
| permission modes table | **kept as advice**; the image disables `bypassPermissions` | `disableBypassPermissionsMode` in the managed settings, unchanged |
| subagents for heavy reading | **kept as advice** | already the engine's practice (`docs/PARALLEL.md`) |

## 6. `.claudeignore`

| Guide | Here | Why |
| - | - | - |
| "`.claudeignore` does not exist; use `permissions.deny`" | **agreed**, and both are used | `bee init` adds `Read(...)` deny rules generated from the file (when it has no `!` lines, which deny rules cannot express), takes back the ones it made earlier for patterns since removed, and always denies the sign-in files where they are (`Read(//**/.credentials.json)`, `Read(~/.claude/.credentials.json)`, `Read(~/.beethoven/**/.credentials.json)`: a bare pattern would be relative to the project) and the `.env` rules |
| the `PreToolUse` hook script | **rewritten** (`claudeignore.py`) | the guide's script runs `grep -q "$TARGET_PATH" .claudeignore`: it looks for the path's text inside the ignore file, so `node_modules/` never matches `src/node_modules/x.js`, an absolute path matches nothing, and a path that happens to be a substring of a comment is refused. `claudeignore.py` implements gitignore's rules (anchors, `**`, `*`, `?`, classes, directory-only patterns, `!` re-inclusion, "a file inside an excluded folder cannot be re-included"), judges Read, Edit, Write, MultiEdit, NotebookEdit, Grep, Glob and LS, resolves links before judging, and always refuses `.credentials.json` |
| the hook returns a JSON deny with a reason | **adopted** | the model reads the reason and chooses another path; a malformed event fails open with a note on stderr, because the deny rules and the guard stand behind it |
| `sandbox.filesystem.denyRead` | **not adopted** | not checked against the sandboxing page in this change, and Claude Code's sandbox is unset in the image (whether it nests in Apptainer is not yet measured, `containers/README.md`) |

## 7. Where it runs, and what it changes outside its own folder

Everything is in `src/beethoven_research/bee/`, `tests/bee/` and `docs/bee/`, so it can move to the
agents repository of the split (`/mnt/project-files/plan/REPO_SPLIT.md`, `beethoven-agents`) in
one piece. `handoff.py` and `claudeignore.py` import nothing of the package, because the image runs
them as files. The places it had to touch outside that folder, each small:

- `containers/managed-settings.json`: the hooks and the status line, run with the image's python,
  isolated (`python -I`), from `/opt/beethoven-research/hooks/`; the guard runs the same way, so
  neither `PYTHONPATH` nor a module file in the bound project stands in for one they import; deny
  rules for `claude auth login` and
  `claude project purge`. `allowManagedHooksOnly` is on in the image, so a project's own hooks
  never run there; these are the ones that do.
- `containers/claude-verify.def`: bakes the two files beside the guard and smoke-tests both; the
  `%help` text no longer shows the sign-in bound read-only (Claude Code writes its transcripts
  and refreshed sign-in there, `containers/README.md`).
- `src/beethoven_research/guard.py` and `tests/test_guard.py`: the guard refuses `claude auth login`
  (a sign-in is the student's own sitting, and `--console` bills per use) and
  `claude project purge` (it deletes Claude Code's own records for a project, which live in the
  sign-in directory), with the trigger tokens `login` and `purge`.
- `.github/workflows/ci.yml`: the image lane runs both hooks inside the built image and
  `bee-grid.sh list` against it.
- `pixi.toml` and `pixi.lock`: the laptop platforms (`win-64`, `osx-arm64`, `osx-64`) and a `bee`
  task. Re-locked with pixi 0.81.0; every one of the 70 `linux-64` entries is byte for byte the
  same as before, so the image and CI reconstruct the same environment.

## 8. What bee checks before it starts a session

An adversarial review of the first version (four reviewers, each finding then re-checked by a
second) found what these close; every one has a test in `tests/bee/`.

- **The transcript of a fork.** `session.json` sits in the project, which a session can write, so
  `continue --fork` believes it only for a file named after the recorded session in the projects
  folder of the account that wrote it. On the grid the script resolves the path again on the host,
  links included, before it binds anything. A path to a sign-in file or to a key under `$HOME` is
  refused, never bound or resumed.
- **A project that would bill another way.** A project whose `.claude/settings.json` or
  `settings.local.json` names `apiKeyHelper`, `awsCredentialExport`, `awsAuthRefresh` or
  `gcpAuthRefresh`, or sets any credential variable in `env`, is refused before anything starts
  (names only; no value is read out). Each of those outranks the subscription.
- **Where it runs.** A Slurm job id is not enough: an `salloc` shell has one and still runs on the
  login node, so bee and `bee-grid.sh` also require the node slurmd names for a job step. On the
  grid the agent markers (`CLAUDECODE` and the two request ids) are checked before `--cleanenv`
  hides them, as on the laptop.
- **One project, one place.** The grid binds each project at `/work/<folder>-<hash>`, so what Claude
  Code records per folder (the trust answer, approved servers) belongs to that project. Each call
  gets its own node-local temp, so a second call in the same job never removes a running session's
  `/tmp`.
- **The limit record.** The rest counts from the hit, not from when bee next runs; a record whose
  window has reset rests nothing; the record keeps only its own session's reading; and a session
  that goes on after a limit (Claude Code waits for the reset, or the person types again) retires
  it at its next finished turn.

## 9. Not yet measured

- Bash 4.2 (the grid's CentOS 7): `bee-grid.sh` is written to that subset and tested under the
  runner's bash with a stand-in `apptainer`; a real run on a compute node is the runbook's step G3.
- That the status line's `rate_limits` arrive on greg's Max plans on the grid as documented.
- That apptainer 1.2.2 on the grid makes the mount points `/work/<folder>-<hash>` and
  `/handoff/<id>.jsonl` inside the read-only image (it needs its underlay or overlay); the
  runbook's first `bee-grid run` shows it.
- Probe M13 (ACCOUNTS.md section 9): where a forked continuation is written, and its first turn's
  cost.
- Whether `claude auth status` inside the image answers for a directory bound at `/claude` exactly
  as on a laptop (expected; the CI lane has no sign-in to try it with).
