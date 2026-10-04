# Accounts and sign-in (decision D4)

greg's decision of 2026-10-04 02:30Z (after the card he answered at 02:18Z, "my own several
accounts"): the engine runs on several Claude accounts that he holds in his own name, at once, one
master node per account, with one account holding the super-master role at a time and handing it
over as its usage window nears its limit, "in the claude-swap style". This document is the
repository's side of it: the rule, the directories, the one sitting per account, and what a job
does and never does. The design of the masters, the super-master, the registry and the hand-off is
BUILD_PLAN §17 and Linear BEE-185 (the re-plan thread's), and this file follows them, not the
other way round.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.

## 1. What changed and what did not

Withdrawn: "one person, one account". A person who holds several subscriptions runs them all.

Unchanged, with the same rank as before:

- every account is a flat-rate subscription in the person's own name; no pooled or commercial API
  key, no gateway, no Bedrock or Vertex (D1);
- usage credits are never used, on any account (CLAUDE.md, non-negotiables);
- nothing of ours ever reads, stores, copies, prints, moves, links or relays a credential, or
  writes inside a sign-in directory; the engine starts Claude Code where a sign-in already is and
  asks only `claude auth status`;
- no account is ever used by anyone but its holder, and no account is shared.

## 2. Where the sign-ins live

| Directory | Whose | Made by |
| - | - | - |
| `$HOME/.beethoven/claude` | the launcher's (the site profile's `claude_config_dir`; the interactive Remote Control worker) | `hpcworker claude login` |
| `$HOME/.beethoven/accounts/<label>/claude` | the engine's, one per account | one sitting per account (section 3) |

`<label>` is the student's own word for the account: lower-case letters, digits and hyphens, at
most 32 characters (`main`, `second`, `lab-2`). The sorted labels are the rotation order, so a
student who wants a particular order numbers them (`01-main`, `02-second`). The engine's account
list is nothing more than the directories under `$HOME/.beethoven/accounts/` whose `claude auth
status` answers signed in; there is no registry file to keep in step.

An account the launcher already uses may serve the engine too: sign it in again under a label.
Two sign-ins of one account are two sessions of one person, which is what a laptop and a phone
already are.

## 3. One sitting per account

The sitting is the launcher's (`hpcworker claude login`, the one place a student signs in on the
grid) with one difference: the directory. It runs on a compute node inside the image, never on
the login node, and `claude auth login` does the whole of it; the student follows the link Claude
Code prints and pastes the code back. The engine's steps do not use Remote Control, so the
launcher's second question ("Enable Remote Control?") is not part of an engine account's sitting.

Until the launcher gains the option (section 6), the sitting is typed by hand inside an
interactive allocation on a compute node (the launcher's own sitting makes one; at Wayne State the
test partition is `secp` with `--qos=secondary`):

```sh
label=main                                   # the student's word for this account
dir="$HOME/.beethoven/accounts/$label/claude"
mkdir -p "$dir" && chmod 700 "$dir"
module load apptainer/1.2.2                  # Wayne State; other sites differ
apptainer exec --containall --cleanenv --no-home \
    --bind "$dir":/claude \
    --bind "/tmp/$USER/signin_$SLURM_JOB_ID":/tmp \
    --env CLAUDE_CONFIG_DIR=/claude \
    /rs/rs_grp_oschome/shared/beethoven/containers/claude-node22 \
    claude auth login
```

Then, the same way, `claude auth status` must answer `loggedIn: true`. The directory is created
empty by the student for Claude Code to fill; nothing of ours writes into it afterwards. `mkdir -p
/tmp/$USER/signin_$SLURM_JOB_ID` first, and remove it when the sitting ends: node-local temp, the
one place besides our run directories that we delete.

## 4. What a job does

- The orchestrator chooses the account for the job (the super-master's holder for a super-master
  job; the master's own account for a master and for every session it starts) and the job binds
  that one directory, read-write, at `/claude` with `CLAUDE_CONFIG_DIR=/claude`, exactly as
  `containers/README.md` shows with `$ACCOUNT` as the label. A job binds one sign-in directory and
  never two.
- Before any step, the job reads `claude auth status`. A directory that does not answer signed in
  is skipped and reported in the run's record (label, time, the status line); it is never
  "repaired", and `claude auth login` is never run by a job, because a sign-in is the student's
  own sitting at a terminal.
- A step's usage-limit hit is the pacing's (CLAUDE.md, token economy): the step is resubmitted
  after the window rolls on the same account, or the super-master hands over (section 5). It is
  never a retry loop, and never a switch of account inside a running step.

## 5. The super-master mutex and the hand-off

The super-master role is a file under the run root (`BEETHOVEN_RUN_ROOT`), written by the holder,
naming the holder's label, the time it took the role, and where the saved top-level state is. A
hand-off is three things, in this order: the holder writes its top-level state (what it was
doing, what is in flight, what each master owes it), the holder writes the new holder's label into
the mutex file, and the next super-master job, started on the new holder's account, reads both
and continues where the last stopped. Nothing is copied between sign-in directories: the only
thing the hand-off changes about sign-ins is *which directory the next job binds*.

The hand-off threshold ("as it nears its session usage limit") needs a number the engine does not
have yet: how Claude Code reports the remaining window is **not yet measured** (Linear BEE-121
measures it first). Until then the plan sets the rule and nothing here claims a figure.

Masters, sessions and agents, and the registry that keeps any of them from being orphaned, are in
BUILD_PLAN §17; `docs/PARALLEL.md` section 4 says what Claude Code's own teams and messaging can
and cannot do inside a batch job.

## 6. The launcher's contract item (not yet built)

`hpcworker claude login --account <label>`: the launcher's existing sitting with
`CLAUDE_CONFIG_DIR` set to `$HOME/.beethoven/accounts/<label>/claude`, without the Remote
Control question, plus `hpcworker claude status --account <label>`. It belongs to the hpcworker
repository and its contract (`docs/V11_CONTRACT.md` there); nothing in this repository depends on
it, since the by-hand sitting above is the same commands.

## 7. Why not claude-swap's way

claude-swap keeps a copy of each account's Claude Code credential file (and macOS keychain
entry) under its own backup folder and swaps the live one when you switch. That is a credential
read, copied and written by a tool, which is exactly what the non-negotiable forbids here, on any
account, for any reason. The engine takes the idea, one account at a time holding the role and
the state carried across the switch, and not the mechanism: each account's sign-in stays where
Claude Code put it, and switching is choosing which directory the next job binds.

## 8. The terms, as a record

Read on 2026-10-04 by the skeleton thread. Anthropic's Consumer Terms (2025-10-08) forbid sharing
an account or making it available to anyone else; this design does neither. Its Usage Policy
(2025-09-15), under "Do Not Abuse our Platform", lists intentionally bypassing capabilities,
restrictions or guardrails established within its products. The card in the re-plan thread said
so before greg chose, and he decided with it on the table. This is a record of what was read and
decided, not a gate, and no text here may claim the design is more than that.

## 9. Not yet measured

- How Claude Code reports the remaining usage window from a `-p` step (BEE-121).
- Whether a second `claude auth login` of an account already signed in elsewhere behaves as a
  plain second session (expected from the laptop-and-phone case; not run on the grid).
- The cost in wall time of the by-hand sitting at Wayne State (the launcher's sitting is measured;
  this one is the same commands with another directory).
