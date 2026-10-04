# bee: setting up and switching accounts

The steps greg types, on his own machine and on the grid. What each step does and why is in
[STATE_HANDOFF.md](STATE_HANDOFF.md); the account design is [../ACCOUNTS.md](../ACCOUNTS.md).
Nothing here asks for a password, a code or a key except Claude Code's own sign-in, which shows its
link in the terminal and takes the code back there; bee never sees either.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs; "Open OnDemand" is the mark of its publisher.

## Laptop, once

**L1. pixi** (the version that made the lock):

```sh
curl -fsSL https://pixi.sh/install.sh | PIXI_VERSION=v0.81.0 bash        # macOS, Linux, WSL
```

```powershell
$env:PIXI_VERSION = 'v0.81.0'; iwr -useb https://pixi.sh/install.ps1 | iex # Windows PowerShell
```

**L2. The engine checkout and its environment** (Windows, macOS on Apple silicon or Intel, Linux):

```sh
git clone https://github.com/OpenSpineConsortium/beethoven-research
cd beethoven-research
pixi install --locked
```

**L3. The shell helpers.** Add one line to the shell's start-up file, with the checkout's real path:

| Shell | File | Line |
| - | - | - |
| Git Bash (Windows), bash | `~/.bashrc` | `. ~/beethoven-research/src/beethoven_research/bee/shell/bee.sh` |
| zsh (macOS) | `~/.zshrc` | the same line |
| PowerShell | `$PROFILE` | `. $HOME\beethoven-research\src\beethoven_research\bee\shell\bee.ps1` |

Open a new terminal. `bee`, `bee-use <label>` and `bee-which` now exist. (Without the helpers,
`pixi run bee <args>` in the checkout is the same command.)

**L4. The accounts.** One line per account, once per machine:

```sh
bee account add personal --default-location --no-login   # the account already signed in at ~/.claude
bee account status personal                              # must say: signed in (you@..., max)
bee account add work                                     # a new directory; Claude Code's own sign-in starts
```

`add` with no `--default-location` makes `~/.beethoven/accounts/<label>/claude` (0700) and runs
`claude auth login` there: open the link in a browser signed in to *that* Claude account, approve,
paste the code into the terminal. The label is your own word: lower-case letters, digits and
hyphens. `bee account add main` links the launcher's `~/.beethoven/claude` when it exists on this
machine. `bee account list` shows them all.

**L5. Each project you work in, once:**

```sh
cd ~/code/some-project
bee init
```

This writes into `.claude/settings.local.json` (kept out of git through `.git/info/exclude`) the
state hooks, the `.claudeignore` hook, deny rules for sign-in files and `.env`, and bee's status
line; and, when absent, `.claude/commands/handoff.md`, `.claude/commands/hydrate.md` and a
`.claudeignore`. Running it again adds nothing. On Windows the hooks run under Git Bash, which
Claude Code uses for hooks there.

## Laptop, every day

| You want | Type |
| - | - |
| start work on an account | `bee run` (the pinned or current account, else the first) or `bee run work` |
| see where you are | the status line: `bee:work · 5h 63% until 14:05 · 7d 22% · ctx 41%` |
| the status line says `/handoff, then exit and run bee continue` (90 % of the 5-hour window) | type `/handoff` in the session; when it has written the file, exit and type `bee continue` |
| the session stopped on a usage limit | when Claude Code exits, bee asks "Continue on the next account? [Y/n]"; press Enter (or type `bee continue` later) |
| continue on a particular account | `bee continue personal` |
| carry the whole conversation instead of the hand-off file | `bee continue --fork` (an uncached first turn; see STATE_HANDOFF.md section 3) |
| context at 60 % | `/compact focus on: <the open problem>, <the decision to keep>`; the status line reminds you |
| switch the plain `claude` in this shell | `bee-use work`, then `claude` as usual |
| always use one account in one project | `bee account pin work` (writes a block into `.envrc`; then `direnv allow`); `bee account unpin` |
| take an account out of the rotation for a while | `bee account disable work`, later `bee account enable work` |
| say an account is spent (and until when) | `bee account spent work --hours 3`; `bee account rested work` undoes it |
| see what happened | `bee handoff log` (the hand-off and switch logs) |

**From claude.ai to Claude Code.** In the claude.ai conversation you are leaving, paste the output of
`bee handoff prompt --web` as the last message. Save the reply (one fenced block) as a file, then:

```sh
bee handoff import reply.md     # placed as .claude/state/claude_context.md, secrets replaced
bee handoff check               # the six sections, the phase, the next actions
bee continue work               # starts Claude Code with "Read .claude/state/HYDRATE.md and follow it."
```

**From the laptop to the grid, or back.** Commit and push the work. The hand-off file is not in git
(the state folder ignores itself), so copy it across: `bee handoff where` prints the folder, and the
file is `claude_context.md` in it. Put it inside the project on the other side and run
`bee handoff import <file>` there (on the grid, `bee-grid.sh handoff import <file>`). Each machine
keeps its own account list, and each account is signed in once on each machine.

## Grid, once

All of this runs on a compute node, never on the login node. One scheduler call from the login node
gives the shell; everything after it happens inside the allocation.

**G0. The image** (once for the lab; Linear BEE-103, not yet done). In a Slurm job on a compute
node, from the engine checkout:

```sh
module load apptainer/1.2.2
bash containers/build.sh --out /rs/rs_grp_oschome/shared/beethoven/containers
```

`bee-grid.sh` uses `/rs/rs_grp_oschome/shared/beethoven/containers/claude-verify.sif` unless
`BEE_IMAGE` names another. The launcher's `claude-node22` image has no engine environment, so bee
cannot run in it.

**G1. An interactive allocation** (Wayne State's test partition; the portal's compute-node shell
does the same):

```sh
srun -p secp --qos=secondary -t 4:00:00 -c 4 --mem=16G --pty bash
```

**G2. The engine checkout, on the lab volume** (the home is a soft quota), cloned from the compute
node with the account's own `gh` sign-in:

```sh
cd /rs/rs_grp_oschome/$USER
gh repo clone OpenSpineConsortium/beethoven-research
alias bee-grid="bash /rs/rs_grp_oschome/$USER/beethoven-research/src/beethoven_research/bee/bee-grid.sh"
```

**G3. The first run** (this is also the first measurement of the script under the grid's bash 4.2
and apptainer 1.2.2):

```sh
bash -n /rs/rs_grp_oschome/$USER/beethoven-research/src/beethoven_research/bee/bee-grid.sh
bee-grid list                      # "No accounts yet." on a fresh list
```

**G4. The accounts**, once each, on the grid:

```sh
bee-grid add main                  # links ~/.beethoven/claude; already signed in, so nothing to paste
bee-grid add second                # Claude Code's sign-in in the image: open the link, paste the code
bee-grid status                    # every account: signed in (..., max)
```

## Grid, every day

```sh
srun -p secp --qos=secondary -t 4:00:00 -c 4 --mem=16G --pty bash    # or the portal's shell
cd /rs/rs_grp_oschome/$USER/some-project
bee-grid run main                  # Claude Code in the image, as main, with bee's hooks and status line
bee-grid continue                  # after a hand-off or a limit: the next account, from the hand-off file
bee-grid continue --fork           # or: the whole conversation, the transcript bound read-only
bee-grid account spent main --hours 2
bee-grid handoff check
```

Each container sees one sign-in directory, at `/claude`, and the project at `/work`. The image's
managed settings run bee's hooks whether or not the project ran `bee init`. `--run-dir DIR` puts the
state folder in an engine run directory instead of the project. At a terminal, `bee-grid run`
offers the next account when a session stops on a usage limit, as on the laptop.

## Probe M13: what a fork across accounts does (greg, on the grid)

ACCOUNTS.md section 9 lists what this probe settles. It uses Claude Code directly, with the binds
`bee-grid.sh` uses, so what it measures is Claude Code and not bee. Two accounts `a` and `b`, both
signed in (G4); a scratch project; inside an allocation (G1).

```sh
img=/rs/rs_grp_oschome/shared/beethoven/containers/claude-verify.sif
A=$HOME/.beethoven/accounts/a/claude
B=$HOME/.beethoven/accounts/b/claude
P=/rs/rs_grp_oschome/$USER/m13 && mkdir -p "$P" && git -C "$P" init -q
s=/tmp/$USER/m13_$SLURM_JOB_ID && mkdir -p "$s" && chmod 700 "$s"
trap 'rm -rf "$s"' EXIT                        # our node-local temp, the one thing removed
box() { apptainer exec --containall --cleanenv --no-home --bind "$s:/tmp" --bind "$P:/work" --pwd /work "$@"; }

# 1. A headless session on a.
box --bind "$A:/claude" --env CLAUDE_CONFIG_DIR=/claude --env BEE_ACCOUNT=a "$img" \
    claude -p "Remember the word kumquat. Reply with ok and nothing else."
cat "$P/.claude/state/session.json"            # session_id and transcript_path, recorded by the hook
id=<session_id from the file>
t="$A/projects/-work/$id.jsonl"                # the recorded path, seen from the host
sha256sum "$t"

# 2. Fork it under b, the transcript bound read-only.
box --bind "$B:/claude" --bind "$t:/handoff/$id.jsonl:ro" --env CLAUDE_CONFIG_DIR=/claude \
    --env BEE_ACCOUNT=b "$img" \
    claude -p --resume "/handoff/$id.jsonl" --fork-session "Which word did I ask you to remember? One word."

# 3. Record.
sha256sum "$t"                                 # must equal step 1's: the source was never written
cat "$P/.claude/state/session.json"            # the new session id, previous_session, context_tokens
ls "$B/projects/-work/"                        # where the continuation landed (names only)
```

Write down: the answer to step 2 (kumquat or not), both checksums, the new session id and where
its file landed, `context_tokens` and `estimated_cache_write_usd` from step 3's record, and
`claude --version` inside the image. Then the same with an interactive session instead of `-p`
(drop `-p` from both steps and type the question). The results go to ACCOUNTS.md section 9 and
the ledger; `bee continue --fork` stays the second choice until they are on record.
