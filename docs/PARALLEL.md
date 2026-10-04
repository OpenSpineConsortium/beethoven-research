# Parallel work: worktrees, workflows, teams and messaging

How this repository is built in parallel, and how the engine's own parallel parts are written.
Every fact here was checked on 2026-10-04 against the Claude Code pages greg named (worktrees,
workflows, agent-teams, cross-session-messaging) and the overview page that compares them
(`code.claude.com/docs/en/agents`), after his asks of 02:30Z ("agent teams ... teams of teams ...
new sessions can be new Slurm jobs ... don't orphan sessions/agents") and 02:37Z ("incorporate
worktrees and workflows for bootstrapping and writing the parallel parts of the ecosystem"). Where
a sentence says "not yet measured", nothing here has run it on the grid.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.

## 1. The five ways Claude Code runs work in parallel, and which one is which here

| Way | What it is (the overview page) | Here |
| - | - | - |
| Subagents | workers inside one session, own context, return a summary | inside a step and inside a build session; the only fan-out a `-p` step has |
| Agent view | one screen to dispatch and watch background sessions on a machine | a person's laptop only |
| Agent teams | a lead session supervising peer sessions, shared task list, messaging; experimental, interactive only | a master's in-process team on its node (tier 1, BUILD_PLAN §17.2.3); never from a `-p` step (section 4) |
| Projects | threads in the cloud or over Remote Control, from claude.ai/code | the build itself (this project) |
| Dynamic workflows | a script that runs many subagents and cross-checks them; runs in `-p` too | repeatable orchestration, saved in `.claude/workflows/` |

Two tools support them without being a way to run agents: worktrees (a separate git checkout per
parallel editor) and cross-session messaging (plain text between a person's own sessions).

## 2. The build in parallel: worktrees

A git worktree is a second working directory on the same repository, with its own branch. Two
sessions editing the same checkout overwrite each other; two sessions in two worktrees never do.

**What is set in this repository**

- `.gitignore` ignores `.claude/worktrees/`, where Claude Code creates them by default.
- `.claude/settings.json` sets `worktree.baseRef` to `"head"`: a new worktree branches from the
  current HEAD of the checkout it was made from, so a stream on `claude/project-thread-...` carries
  that branch's commits. The default, `"fresh"`, would branch from `origin`'s default branch and lose
  them. Inside a worktree, `"head"` is that worktree's HEAD.
- `.claude/agents/stream.md` is a subagent definition with `isolation: worktree`: every stream spawned
  from it gets a worktree of its own, automatically removed when it ends without changes and kept
  (and later swept) when it has changes. Ask for "a stream for X" or "use worktrees for your agents".
- No `.worktreeinclude`: it copies gitignored files such as `.env` into new worktrees, and this
  repository keeps no such file by rule (secrets never enter it).

**What a stream must know**

- A worktree is a fresh checkout: `.pixi/` is not there. `pixi install --locked` first (the pixi
  cache is shared across worktrees, so it is minutes, not a new solve); `pixi run gate` then works
  in the worktree, and so does the pre-push gate, because `core.hooksPath` is repository
  configuration shared through the one `.git` directory.
- `${CLAUDE_PROJECT_DIR}` in a hook command stays the main checkout; the worktree path arrives as
  `cwd` in the hook's JSON. The session-start hook here runs against the main checkout, which is
  what we want (it sets `core.hooksPath` once for every worktree).
- A subagent in a worktree does not load the worktree's own `CLAUDE.md` or `.claude/rules/`; it
  carries the instruction files of the conversation that started it.
- Claude Code refuses, inside an isolated session, any edit of a path in the main checkout and any
  git command that would act on it (`git -C`, `GIT_DIR`, a `cd` into it). A refused command is
  rewritten as plain separate commands, never worked around.
- "Yes, don't ask again" answered in a worktree is saved to the main checkout's
  `.claude/settings.local.json` and applies to every worktree.

**A person's own parallel sessions**

```sh
claude --worktree stream-a        # .claude/worktrees/stream-a on branch worktree-stream-a
claude --worktree "#12"           # a worktree on pull request 12's head, at .claude/worktrees/pr-12
claude --worktree stream-a --resume   # back into a kept worktree (printed on exit)
git worktree list                 # what exists
git worktree remove .claude/worktrees/stream-a      # when it is merged; --force if it has changes
```

On exit, Claude Code offers to keep or remove a worktree that has work in it and removes a clean
unnamed one by itself. `claude -p --worktree ...` never cleans up: it leaves the worktree and the
lock it took, so a non-interactive run removes its own worktree with `git worktree remove` (after
`git worktree unlock` if git refuses). Deleting a worktree someone else made is a deletion outside
our own run directories and is not done.

## 3. The build in parallel: dynamic workflows

A dynamic workflow is a JavaScript script that orchestrates subagents; the script holds the loop,
the branching and the intermediate results, so the conversation holds only the final answer. The
`workflow-authoring` bundled skill is the reference for writing one; what matters for this
repository:

- **Saved workflows live in `.claude/workflows/<name>.js`** and run as `/<name>` in any session
  that opened this checkout (after `/reload-skills` in a session already open). The file starts
  with a literal `export const meta = { name, description, phases? }`; the body uses `agent()`,
  `pipeline()`, `parallel()`, `phase()`, `log()` and the `args` global; `Date.now()`,
  `Math.random()` and a no-argument `new Date()` throw, so that a relaunched run replays the same
  `agent()` calls from cache. A script with `import()` fails before it starts. Claude Code refuses
  to save through a symlinked `.claude`, `.claude/workflows` or target file.
- **Two are saved here.** `gate-until-green` runs `pixi run gate`, fixes what fails one round at a
  time (one fixer, because parallel fixers would edit the same files) and stops when green or when
  two rounds make no progress. `verify-module` is the SpecPylot loop of the definition of done
  (step 6): run the CrossHair/Z3 gate, feed each counterexample to a repair agent, repeat until
  proven, then write the contracts out in plain words for a human to review. Both take `args`
  (`{ maxRounds: 0 }` runs no agent and is the syntax check). Neither has yet run against a real
  failure: not yet measured.
- **Limits** (the runtime's, not advice): at most 16 concurrent agents by default, fewer in a
  CPU-limited container, changed with `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (1 to 256,
  Claude Code 2.1.269 or later); 4096 items per `parallel()` or `pipeline()` call, refused above;
  1000 agents per run. `workflowSizeGuideline` (`.claude/settings.json`, `"large"` here) is advice to
  Claude when it writes a script, not a cap.
- **Resume.** A stopped run relaunches from its saved results within the same session; the first
  agent whose prompt changed, and every agent after it, runs again. A cloud session keeps the
  results with its conversation history. A run started fresh has nothing to resume.
- **Cost.** Every agent counts against the plan's usage limits of the account the session runs on.
  An interactive run pauses on a usage limit and resumes when it rolls (twice at most); a `-p` run
  does not pause, the affected agent fails, and the orchestrator's pacing owns the retry.
- **Permissions.** In an interactive session the first launch asks once (auto mode) or every run
  (manual). In `-p` and the Agent SDK nothing asks: the Workflow tool call goes through the
  session's permission rules like any tool, so a `dontAsk` step denies it unless `Workflow` is in
  its allow rules. A workflow agent's prompt is text the script computed, not a request from the
  person, and the auto-mode classifier judges that agent's actions accordingly.
- **The `ultracode` switch** (`/effort ultracode`, `claude --effort ultracode`, or the `ultracode`
  settings key) makes Claude plan a workflow for every substantive task; it also lifts the
  concurrent-subagent limit and the large-run warning. The `ultracode` *keyword* in a prompt is an
  opt-in only when a person typed the prompt; through `-p`, a scheduled task, a webhook or a relayed
  comment it starts nothing. Whether the settings key makes a `-p` step plan workflows on its own is
  not yet measured.
- **Prompt cache in a fan-out.** Agents with the same model, effort, agent type, tools, schema and
  working directory share a cached prefix; Claude Code holds all but the first for up to
  `CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS` (5000) so they read it. Their cache holds five minutes on
  a subscription; `subagentPromptCacheTtl: "1h"` lengthens it and is billed as an hour's write on
  the API, which the engine never pays (decision D1), so it is not set.

## 4. The engine's parallel parts

**One clone per job.** Each Slurm job clones the run's repository onto node-local scratch
(`/tmp/$USER/...`, the cleanup trap), works there, commits and pushes its branch. Jobs never share
a checkout on the parallel filesystem: git's locking under concurrent writers on several nodes of
a CephFS directory is not something git promises, and the per-job clone avoids the question
instead of measuring it. Worktrees belong *inside* a job: a step's subagents may each take one of
the job's clone, and a step that made a worktree removes it by name before the job's exit trap
runs.

**Workflows in a step.** A `-p` step that may run a saved workflow names it in its prompt
(`/verify-module` with its `args`) and carries `--settings` with `permissions.allow: ["Workflow"]`
(section 3, permissions); whether a `-p` prompt that names `/<workflow>` launches it is not yet
measured and is the first thing to measure. `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` is set by
the job to the allocation's cores, beside `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`. The image sets
nothing of this; `disableWorkflows` is deliberately absent from it.

**Teams: what a batch job can and cannot have.** The agent-teams page is plain: spawning teammates
requires an interactive session; in `-p` (and the Agent SDK) Claude spawns no teammate and a named
subagent runs as an ordinary subagent. A session has exactly one team, a teammate cannot spawn a
team ("no nested teams"), the lead is fixed, in-process teammates do not survive a resume, and the
mailbox is a JSON file on one machine. BUILD_PLAN §17.2.2 and §17.2.3 build on exactly that, in two
tiers:

- **Tier 1, Claude Code's own.** A **master** is the launcher's worker shape under one account: an
  always-on Slurm job running an *interactive* Claude Code session under a pseudo-terminal (the way
  the launcher's `claude remote-control` already runs, measured 2026-09-29), with
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, `crossSessionInbound: accept`, in-process teammates only
  and never tmux, CPUs sized for its teammates. It is that account's one team lead. Its lead hooks
  carry the engine's gates (`TaskCompleted` runs the verification gate on the task's files;
  `TeammateIdle` refuses idleness with open assigned tasks). A `claude -p` step never leads a team.
- **Tier 2, the engine's.** A **satellite** is a new Slurm job a master asks for through the engine's
  own tool (role, prompt, account, resources), submitted through the adapter (one `sbatch` per
  action, never from a teammate's shell) and registered with its parent before its first turn. It
  runs under the same account's directory, as `claude -p` for a bounded step or as an interactive
  session under a pseudo-terminal when it needs teammates of its own; a satellite that leads a team
  is the **team of teams**. A team whose members belong to several accounts exists only at this
  tier: its members are sessions under different masters, joined by one task graph in the engine
  store and by the bus, never by one Claude Code team config. Metaflow's `-p` steps stay the
  deterministic pipeline; masters and satellites are the agentic layer those steps call on.

Whether an interactive session under a pseudo-terminal, driven only through its inbox socket with
no keyboard, spawns teammates is not yet measured (§17.4 M1; the page says interactive, not
attended). A person's own laptop session may run a Claude Code team the ordinary way; the setting is
a user setting, not this repository's.

**Messages.** Cross-session messaging is on by itself from Claude Code 2.1.224; a `-p` session
binds an inbox socket like an interactive one (bare mode does not). Three channels and one bridge
(BUILD_PLAN §17.2.5):

| Sender and receiver | Path |
| - | - |
| inside one team on one node | Claude Code's own mailbox files |
| two sessions in the same container on one node | the per-session Unix socket under `/tmp/cc-socks-<uid>` (the node-local `/tmp` the job binds); never through Anthropic servers. Whether two Apptainer instances on one node see each other's sockets, and whether a session with another `CLAUDE_CONFIG_DIR` is listed, are not yet measured (§17.4 M2, M3) |
| a session in a container and one on the host | cannot reach each other |
| sessions on two nodes, one account | Remote Control through Anthropic servers, when both ends are connected; used for notices, not as the bus |
| across nodes and accounts | **the engine's bus**: `messages` and `registry` tables in the engine store with `LISTEN/NOTIFY`, and an append-only inbox file per session on the lab volume as the fallback while the store is down. The **bridge** is a sidecar per session in the job script: it reads the bus and posts into the local session's inbox socket with the session's own token (`CLAUDE_CODE_MESSAGING_SOCKET`, `CLAUDE_CODE_MESSAGING_TOKEN`; in the image Claude Code may be process 1, so the token line is always sent; both reach only the session's own hooks and Bash commands, exported before `SessionStart` runs, so a `SessionStart` hook baked into the image, built with the bridge under BEE-186, writes them to a mode-0600 file in the run directory where the sidecar reads them; the token is a secret under the secrets rule and never goes to a log or an argv), and carries outbound messages from the engine's send tool to the bus. Each master reports its roster to the super-master this way |

Rules the receiving side applies, which our code relies on and never argues with: a message from
another session is never the person's consent, cannot answer a permission prompt, cannot change
settings or `CLAUDE.md`, and a slash command in it is plain text. A `-p` session without
`crossSessionInbound: "accept"` in its `--settings` holds a message five minutes and drops it, so a
step that should listen carries that setting. A same-machine message is refused above about a
million characters; bursts to one inbox are refused at the sender; loops are throttled at the
receiver. Every message is plain text, under a megabyte, carries no secret, and is logged to the
run's record. Nothing of ours asks another session to do what its own session was refused.

**No orphans** (greg, 02:30Z; BUILD_PLAN §17.2.6). One registry row per session: run, account, job
id, node, session id, process id, parent session, role (master, satellite, teammate), heartbeat,
state. The invariants, each with a test: every session has a parent or is a master, and every Slurm
job of a run is in the registry *before* its first turn (the job script registers; submission only
through the adapter); tier 1 teammates live and die with their lead, which asks each to shut down
and waits before it exits, and never tmux; a master's exit path (the wall-time signal, the stop
file) tells its satellites to finish within a grace period, then cancels, through the adapter, only
the job ids the registry says are its own; a satellite whose parent's heartbeat lapses asks the
super-master for adoption, and stops after its turn if not adopted; the super-master's reaper
reconciles the registry against one `squeue` per 60 s floor while a run is live and against
`sacct` at the end; at run end every registered job is stopped, every row closed, the counts go to
the ledger, and a session closed by the reaper rather than by its parent is an INTERVENTIONS
failure row; the hand-off never strands a session, since the roster moves with the state. A session
that is not in a registry is a bug, not a stray.


## 5. Before writing a parallel part, check

1. Which of the five ways is it, and is that way available where it runs (`-p` has subagents and
   workflows; teams and agent view need a person's terminal)?
2. Who edits which files? Separate editors get separate worktrees or separate clones; never two
   editors in one checkout.
3. Where do the results go: a script variable, a registry file under the run root, or the
   conversation? The conversation is the last resort.
4. What bounds it: `maxRounds`, the agent caps, the wall time, the window pacing?
5. What is logged when the bound cuts something (never a silent cap)?
6. How does it end, and who removes what it made (its own worktrees, its own temp, nothing else)?
