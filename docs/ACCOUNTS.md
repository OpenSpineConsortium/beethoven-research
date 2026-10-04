# Accounts and sign-in (decision D4)

greg's decision of 2026-10-04 02:30Z (after the card he answered at 02:18Z, "my own several
accounts"): the engine runs on several Claude accounts that he holds in his own name, at once, one
master node per account, with one account holding the super-master lease (his word: the mutex) at
a time and handing it over as its usage window nears its limit, "in the claude-swap style". The
design is BUILD_PLAN §17 (the re-plan thread's; §17.2.1 accounts, §17.2.2 masters, §17.2.4 the
lease, §17.2.6 no orphans, §17.3 the rules changed) and Linear BEE-185 to BEE-190; this document is
the repository's side of it and follows the plan, not the other way round.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.

## 1. What changed and what did not

Withdrawn: "one person, one account" and "one account at a time". Several of greg's own accounts
run at once; one of them at a time holds the super-master lease.

Unchanged, with the same rank as before (§17.3):

- every account is a flat-rate subscription in the person's own name; no pooled or commercial API
  key, no gateway, no Bedrock or Vertex (D1); no pooled tokens;
- usage credits are never used, on any account (CLAUDE.md, non-negotiables; BUILD_PLAN C7);
- nothing of ours ever reads, stores, copies, prints, moves, links or relays a credential, or
  writes inside a sign-in directory; the engine starts Claude Code where a sign-in already is and
  asks only `claude auth status`;
- no account is ever used by anyone but its holder, no account is shared, nothing evades
  detection, and every switch is written down.

## 2. Where the sign-ins live

An account is a directory, never a file moved. `bee account list` prints labels, paths and each
directory's `claude auth status`, and nothing else until BEE-188 reads the usage window through
Claude Code.

| Directory | Which account | Made by |
| - | - | - |
| `$HOME/.beethoven/claude` | `main`: the launcher's own (the site profile's `claude_config_dir`; the interactive Remote Control worker) | `hpcworker claude login` |
| `$HOME/.beethoven/accounts/main/claude` | the same `main`: a symbolic link **from here to** `$HOME/.beethoven/claude`, made once by the sign-in command; nothing under the existing directory is moved, renamed or copied, and the profile's `claude_config_dir` is unchanged | `bee account add main` (or `ln -s` by hand) |
| `$HOME/.beethoven/accounts/<label>/claude` | every other account, one directory each | one sitting per account (section 3) |

`<label>` is the student's own word for the account: lower-case letters, digits and hyphens, at
most 32 characters (`main`, `second`, `lab-2`). The engine's list of accounts is `accounts.json`
(label, directory path, enabled, order, and nothing from inside any directory; beside the
`accounts/` folder at `$HOME/.beethoven/accounts.json`, this file's choice of place). `order` is
the rotation order; `enabled: false` takes an account out of the rotation without signing anything
out. `claude auth status` per directory is the only read, as the credential rule already allows.

## 3. One sitting per account, once

The sitting is the launcher's (`hpcworker claude login`, the one place a student signs in on the
grid) with one difference: the directory. It runs on a compute node inside the image, never on
the login node, and `claude auth login` does the whole of it; the student follows the link Claude
Code prints and pastes the code back. When the account will run a master with Remote Control, the
launcher's second step (`claude remote-control` once, answer y, Ctrl-C) follows in the same
sitting; an account that only runs `-p` satellites needs no second step.

The one line, typed in the portal's shell, the extension's connect or the desktop plugin
(BUILD_PLAN §17.2.1): `bee account add <label>` (the engine, Linear BEE-185) or
`hpcworker claude login --account <label>` (the framework). Neither is built yet. Until one is,
the sitting is typed by hand inside an interactive allocation on a compute node (the launcher's
own sitting makes one; at Wayne State the test partition is `secp` with `--qos=secondary`):

```sh
label=second                                 # the student's word for this account
dir="$HOME/.beethoven/accounts/$label/claude"
mkdir -p "$dir" && chmod 700 "$dir"
mkdir -p "/tmp/$USER/signin_$SLURM_JOB_ID"   # node-local temp for the sitting, removed below
module load apptainer/1.2.2                  # Wayne State; other sites differ
apptainer exec --containall --cleanenv --no-home \
    --bind "$dir":/claude \
    --bind "/tmp/$USER/signin_$SLURM_JOB_ID":/tmp \
    --env CLAUDE_CONFIG_DIR=/claude \
    /rs/rs_grp_oschome/shared/beethoven/containers/claude-node22 \
    claude auth login
rm -rf "/tmp/$USER/signin_$SLURM_JOB_ID"     # our own node-local temp, the one thing we delete
```

Then, the same way, `claude auth status` must answer `loggedIn: true`, and the label goes into
`accounts.json`. The directory is created empty by the student for Claude Code to fill; nothing of
ours writes into it afterwards. No sign-out is ever needed: "use another account" is "add an
account", so the launcher's `--login fresh` mode of the 11.0.0 contract has no reason left (the
launcher retires it with its 12.0.0 contract change, §17.2.1). Simple means: one paste per account,
once.

## 4. What a job does

- The orchestrator chooses the account for the job (the lease holder's for a super-master job; the
  master's own account for a master and for every satellite it starts) and the job binds that one
  directory, read-write, at `/claude` with `CLAUDE_CONFIG_DIR=/claude`, exactly as
  `containers/README.md` shows with `$ACCOUNT` as the label. A job binds one sign-in directory and
  never two.
- Before any step, the job reads `claude auth status`. A directory that does not answer signed in
  is skipped and reported in the run's record (label, time, the status line); it is never
  "repaired", and `claude auth login` is never run by a job, because a sign-in is the student's
  own sitting at a terminal.
- A master is an interactive Claude Code session under a pseudo-terminal in an always-on Slurm job
  (the launcher's worker shape), that account's one team lead, with in-process teammates only
  (`docs/PARALLEL.md`, section 4). Its run directory records the account label, and every line of
  `windows.log` carries the label, so the paper reports spend per account.
- A step's usage-limit hit is the pacing's (CLAUDE.md, token economy): a spent account's master
  does not die, it hibernates until the reset; its unfinished tasks are reassigned by the
  super-master when they cannot wait. Never a retry loop, never a switch of account inside a
  running step.

## 5. The super-master lease and the hand-off (§17.2.4)

The lease is one row in the engine store: run, holder account, fencing token, expiry; taken with
compare-and-set, renewed every 60 s by the holder's sidecar. Every write the holder makes to run
state carries the token and the store rejects a stale one, so a holder that lost the lease cannot
damage the state. The holder owns the run-level task graph and assigns work to the other masters by
messages.

Hand-off triggers, in order: the holder's account at or past the usage threshold (90 % by default,
claude-swap's number, tunable) once usage is readable; the usage-limit error itself; wall time
within the margin; the lease expiring unrenewed (a crash). The holder finishes or checkpoints its
turn, writes the top-level state (task graph, roster, `STATUS.md`, open questions) with its token,
posts "handing off to <label>" on the bus, and releases the lease naming the successor: the enabled
account with the most remaining window (or the earliest reset) that has a live master. The
successor acquires, reads the state, announces, and continues. Every switch is logged.

**The session carries over too** (greg, 2026-10-04 03:23Z; this supersedes §17.2.4's "never a
resumed Claude Code session" and waits on the re-plan thread's amendment of the plan). Claude Code
resumes a session from the absolute path of its transcript file (`--resume <path>`, the CLI
reference, read 2026-10-04), and `--fork-session` gives the continuation a new session id. The
holder's transcript is at a path the engine computes without listing anything:
`<its sign-in directory>/projects/<cwd slug>/<session id>.jsonl`, the session id being the one the
job chose with `--session-id`. The successor's job binds that one file read-only
(`--bind <path>:/handoff/<id>.jsonl:ro`) beside its own account's directory at `/claude`, and
starts Claude Code with `--resume /handoff/<id>.jsonl --fork-session`: the continuation's
transcript lands under the successor's directory, the holder's file is never written, and no
credential moves. The only thing the hand-off changes about sign-ins is still *which directory the
next job binds*, plus one transcript read. This is what claude-swap's `--share-history` is for,
without sharing a history folder. What it costs: the first turn after the hand-off re-sends the
whole context, because the prompt cache belongs to the account, so the holder compacts before it
hands off when it can; the master's in-process team does not carry over (the successor re-spawns
its teammates), satellites are registry rows and do. Whether a resume from a read-only path with
the fork works under another account's `CLAUDE_CONFIG_DIR`, and where the fork's transcript lands,
is not yet measured (section 9).

The threshold needs a number the engine does not have yet: how Claude Code reports the remaining
window is **not yet measured** (Linear BEE-121 measures it first; BEE-188 builds the reading).
Until then the hand-off is reactive (the usage-limit error) and nothing here claims a figure.

## 6. Why not claude-swap's way

claude-swap keeps a copy of each account's Claude Code credential file (and macOS keychain
entry) under its own backup folder and swaps the live one when you switch. That is a credential
read, copied and written by a tool, which is exactly what the non-negotiable forbids here, on any
account, for any reason. The engine takes the idea, one account at a time holding the role with the
state carried across the switch and a threshold switch on the usage numbers, and not the mechanism:
each account's sign-in stays where Claude Code put it, and switching is choosing which directory
the next session binds. The usage numbers come through Claude Code itself, never through a token
(§17.2.8).

## 7. The terms, as a record

Read on 2026-10-04 by the skeleton thread. Anthropic's Consumer Terms (2025-10-08) forbid sharing
an account or making it available to anyone else; this design does neither. Its Usage Policy
(2025-09-15), under "Do Not Abuse our Platform", lists intentionally bypassing capabilities,
restrictions or guardrails established within its products. The card in the re-plan thread said
so before greg chose, and he decided twice (the card at 02:18Z, the message at 02:30Z) with it on
the table. This is a record of what was read and decided (BUILD_PLAN D1d keeps it), not a gate, and
no text here may claim the design is more than that.

## 8. What the engine takes from the account switchers, and what it refuses

greg's message of 03:09Z (re-plan thread): the switching should feel like the tools in his four
links, "whichever best integrates with my architecture, native claude code parallel tools, and
permits multiple accounts to work together". The four, read 2026-10-04: claude-swap
(`github.com/realiti4/claude-swap`, its README), the claude-swap guide at umesh-malik.com, Cloak
(`dev.to/synth1s`, "How I solved Claude Code's biggest missing feature") and "Back up your Claude
Code infrastructure" (`dev.to/empiree`). Two mechanisms are on offer. claude-swap keeps a copy of
each account's OAuth token (plus the account section of `.claude.json`) in its own vault and writes
it over Claude Code's live credential at each switch, holding Claude Code's credential locks and
refreshing the target's token first. Cloak sets `CLAUDE_CONFIG_DIR` to one profile directory per
account and, in its author's words, "Nothing is copied, moved, or overwritten." **The engine is
Cloak's mechanism with claude-swap's surface.** Behaviour by behaviour:

| Behaviour | Source | Here | Why |
| - | - | - | - |
| one directory per account, selected with `CLAUDE_CONFIG_DIR`; nothing copied, moved or overwritten | Cloak | **adopted** (section 2) | it is the standing credential rule, kept by construction |
| concurrent sessions, each terminal on its own account through its own environment variable | Cloak, claude-swap `run N` | **adopted**: every master and satellite is one session under one account's directory | "multiple accounts work together" is several sessions at once, not one credential swapped under one session |
| profile name validation, 0700 directories, tokens never logged or transmitted | Cloak | **adopted** (labels: lower-case letters, digits, hyphens; `chmod 700`) | |
| `add` | both | **adopted as `bee account add <label>`**, which is the sign-in sitting of section 3, never "register the currently logged-in account" by copying its credential | the student signs in once per account; nothing of ours reads the result |
| `list` dashboard: each account's 5-hour and 7-day usage with reset times | claude-swap | **adopted as `bee account list`**, read through Claude Code itself (§17.2.8, BEE-121 and BEE-188), never through a token | the numbers are what the lease needs; how Claude Code exposes them is not yet measured |
| `status`, `enable`, `disable` | claude-swap | **adopted** (`bee account status`, `enable <label>`, `disable <label>` edit `accounts.json`'s `enabled`) | a disabled account leaves the rotation without a sign-out |
| `remove` | claude-swap | **adopted for the list entry only**: `bee account remove <label>` edits `accounts.json` and never deletes the directory | nothing is deleted outside our run directories |
| `alias` | claude-swap | not needed | the label is the name |
| `auto` with a threshold (default 90 %), `--strategy best` (most quota left) or `consume-first` (earliest reset), a cooldown (5 min) and a hysteresis margin, `--once` for cron | claude-swap | **adopted into the super-master hand-off** (section 5): threshold 90 % by default, successor = most remaining window or earliest reset, and a cooldown plus hysteresis so the lease does not flip-flop near the threshold; `--once` is the sidecar's one check per 60 s | the hand-off is the one place the engine "switches"; it moves a role, not a credential |
| `run N --share-history` | claude-swap | **adopted in a narrower form** (greg, 03:23Z): the successor resumes the holder's session from the absolute path of its transcript, bound read-only, with `--fork-session` (section 5) | one file crosses, read-only, at the hand-off; no history folder is shared and nothing is copied into a sign-in directory |
| `map` / `bind` a directory to an account | claude-swap, Cloak | not adopted | the orchestrator chooses the account per job; a path never does |
| backing up tokens, `export`/`import` of accounts, writing over the live credential, holding credential locks, refreshing a target's token before a switch | claude-swap | **refused**, all of it | every one reads, copies or writes a credential; the non-negotiable, and claude-swap's guide itself quotes the Consumer Terms on credentials |
| quarantining an account whose refresh token has died | claude-swap | **adopted without touching anything**: an account whose `claude auth status` is not signed in is marked `enabled: false` in `accounts.json` and reported; the student signs it in again | the same effect, by the one read the rule allows |
| `tui` / `watch`, the live dashboard | claude-swap | the client's: the extension's status view and the desktop plugin read the per-account fields the master writes to `status.json` (the desktop thread's proposal) | the engine writes the numbers; the clients draw them |
| `purge` | claude-swap | **refused** | nothing of ours deletes a sign-in directory, ever |
| keep Claude Code's "infrastructure" (CLAUDE.md, agents, skills, commands, hooks, rules, settings) in a private git repository; exclude `.credentials.json`, transcripts, history | the backup post | **adopted and already so**: this repository holds the engine's `CLAUDE.md`, `.claude/agents`, `.claude/workflows`, hooks and settings; nothing inside a sign-in directory is ever backed up by us | the post's exclusion list is the list of what we never touch; a student's own backup of their directories is their business |
| IDE extensions ignore `CLAUDE_CONFIG_DIR` (Cloak's known limitation) | Cloak | noted for the desktop thread | the engine runs Claude Code's CLI in jobs, where the variable is honoured; a client that embeds Claude Code must be checked |

The one thing none of the four does and the engine must: several accounts working on one task
graph at once, with a lease that moves between them (section 5). That is §17's, not a switcher's.

## 9. Not yet measured

- How Claude Code reports the remaining usage window from a `-p` step and from a session under a
  pseudo-terminal (BEE-121; §17.4 M4).
- Whether a second `claude auth login` of an account already signed in elsewhere behaves as a
  plain second session (expected from the laptop-and-phone case; not run on the grid).
- Whether the team directories (`teams/`, `tasks/`) follow `CLAUDE_CONFIG_DIR` rather than
  `~/.claude` (§17.4 M9); it matters for where Claude Code's own writes land, not for ours.
- The cost in wall time of the by-hand sitting at Wayne State (the launcher's sitting is measured;
  this one is the same commands with another directory).
- A resume from the absolute path of a transcript bound read-only, with `--fork-session`, under
  another account's `CLAUDE_CONFIG_DIR` (section 5): whether it starts, where the fork's transcript
  lands, and which Claude Code version added the path form (the CLI reference does not say; the
  image installs the current release at build time).
- How the engine learns a forked master's new session id without listing its directory (a `-p`
  step reports it in its `init` event; an interactive master is the open case).
