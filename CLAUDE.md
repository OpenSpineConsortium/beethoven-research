# Beethoven research engine — working context

This repository is the **autonomous prompt-to-paper research engine**: a headless `claude -p`
research loop, orchestrated by Metaflow as Slurm jobs inside a hermetic Apptainer image, that turns
a research prompt into a verified, reproducible, archived result. It is the compute **peer** of the
Beethoven launcher (`OpenSpineConsortium/hpcworker` + `OpenSpineConsortium/beethoven-extension`),
which is the student's interactive Remote Control worker. The two share a grid, a container image,
the node-local-scratch discipline and the launcher's non-negotiables; they do not share the loop
(interactive Remote Control for the launcher, headless `claude -p` here).

Vendor: Gregory Schwing, trading as OpenSpineConsortium. Beethoven runs Claude Code and is not an
Anthropic product; "Claude" and "Claude Code" are Anthropic's marks, used here to say what it runs;
"Open OnDemand" is the mark of its publisher.

## Read first

- `/mnt/project-files/plan/BUILD_PLAN.md` (the re-evaluated build plan — §3 the definition of done,
  §4 the token economy, §2 the conflicts that are excluded) and `LINEAR_BREAKDOWN.md` (the issues).
  The plan governs; this file is its standing summary for anyone working in the repo.
- `hpcworker`'s `CLAUDE.md` for the grid facts and the launcher's non-negotiables, which hold here.
- The Linear workspace (team *Beethoven*): P0 is the substrate and decisions; G1–G7 are the goals.

## Decisions that are settled (do not reopen)

- **D1 — own subscriptions, no keys.** Every `claude -p` step draws on a flat-rate Claude plan held
  in the **student's own name**, the same rolling 5-hour window their manual sessions use. No pooled
  or commercial API keys, no LiteLLM gateway, no Bedrock/Vertex, no key rotation. Goal 3 is
  quota-window-aware pacing, not routing. A person may hold several such plans and run them all (D4).
- **D2 — the engine is this repository.** The launcher stays in its two repositories.
- **D3 — Metaflow + `outerbounds/metaflow-slurm`**, with a plain `sbatch` adapter underneath first.
- **D4 — several accounts of one person; one master per account; one super-master lease at a time**
  (greg, 2026-10-04 02:30Z, after the card of 02:18Z; the one-account rule of the day before is
  withdrawn; the design is BUILD_PLAN §17, the re-plan thread's, and this file follows it). Several
  of greg's own accounts run at once, each signed in by him into its own directory
  (`$HOME/.beethoven/accounts/<label>/claude`; the launcher's `$HOME/.beethoven/claude` is the
  account `main`, reached by a link, nothing moved), one master job per account, one account holding
  the super-master lease (greg's word: the mutex) at a time and handing it to the next as its window
  nears its limit; the run carries over from the engine's saved state, and (greg, 03:23Z) the
  holder's own session carries over with it: the successor resumes it from the transcript file of
  the session that reached its limit, bound read-only into the successor's job, with
  `--fork-session` (`docs/ACCOUNTS.md` section 5; this supersedes BUILD_PLAN §17.2.4's "never a
  resumed Claude Code session" and waits on the re-plan thread's amendment). The engine holds
  directory paths and labels only (`accounts.json`: label, path, enabled, order), reads inside a
  sign-in directory nothing but the one transcript whose path the session's own hook recorded, and
  never copies between them (claude-swap's credential backup is exactly what we do not do; the switch is
  `CLAUDE_CONFIG_DIR` selection for the next session), logs every switch,
  and never uses anyone else's account. No pooled tokens, nothing that evades detection, usage
  credits never on any account; a spent account hibernates until its reset. Record, not a gate:
  Anthropic's Usage Policy of 2025-09-15 (read 2026-10-04) lists under "Do Not Abuse our Platform"
  intentionally bypassing capabilities, restrictions or guardrails established within its products,
  and its Consumer Terms of 2025-10-08 forbid sharing an account or making it available to anyone
  else, which this design does not do; greg decided with both on the table (BUILD_PLAN D1d, §17).

## Non-negotiables (carried from the launcher; they bind the engine too)

- **Never touch a Claude credential.** No `setup-token`, no `CLAUDE_CODE_OAUTH_TOKEN`, no reading
  `~/.claude/.credentials.json` or the credentials in any sign-in directory. The engine starts
  Claude Code where one of the student's own sign-ins already is and reads only `claude auth
  status`. One person, their own accounts (D4): a job binds exactly one sign-in directory, nothing
  of ours ever copies, moves or links a credential between two of them, and no account is used by
  anyone but its holder. Nothing of ours sets `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` (the VS
  Code edition's paid overflow is excluded, BUILD_PLAN §2 C8, Linear BEE-191); the image unsets both
  and the guard refuses a line that sets them.
- **Usage credits are never used.** greg's rule of 2026-10-04, with the same rank as every other
  bullet here (BUILD_PLAN §2 C7): no run draws on, buys or turns on usage credits for any account,
  and nothing in this repository treats them as an option or a default. A spent window is waited
  out until it resets (Goal 3 pacing, Linear BEE-121); what else may pick a run up is the plan's
  decision D1d, not this file's.
- **No code on claude.ai and no interception of it.** The engine never injects into, proxies or
  parses claude.ai traffic. This was settled for the whole project; the notebook analyses that
  proposed it are excluded (BUILD_PLAN §2, C1).
- **Secrets never appear on a command line, in argv, in logs, in job files or in `sbatch --export`.**
  Never echo one. A per-job token, if one is ever needed, is a mode-0600 file. The `.mcp.json` here
  reads tokens from the environment and hardcodes none.
- **No remote code.** No `eval`, no `new Function`, no fetched-and-run scripts.
- **Nothing is deleted outside the engine's own run directories and node-local temp.** Never the
  Claude Code login directory, the workspace, another run, or anything else under `$HOME`. A
  destructive subcommand must refuse when `CLAUDECODE`, `BEETHOVEN_REQUEST_ID` (or the older
  `ASHLEY_REQUEST_ID`) or a similar agent marker is in the environment, or when stdin is not a
  terminal. This is the lesson of the 2026-09-28 `~/.ashley` deletion. The PreToolUse guard
  (`src/beethoven_research/guard.py`, baked into the image and named by its managed settings,
  Linear BEE-123/BEE-124) refuses it in the agent's shell and file tools, and the Z3 gate proves
  the guard never lets a write into the sign-in directory through and never blocks on anything but
  its listed triggers.
- **Grid etiquette.** Nothing container-related on the login node; container temp and cache on the
  compute node's local disk (`/tmp/$USER/...` with a cleanup trap — on Wayne State `$TMPDIR` is NOT
  node-local, it is CephFS, so never trust it blindly); IPC under `/dev/shm`; client-side scheduler
  polling never faster than 60 s (read the status file, do not re-`squeue`); every job has a wall
  time. Attribute these exactly when writing for anyone outside (see `hpcworker/CLAUDE.md`).
- **Legal boundary (Goal 4) is governance only, on-grid.** On the grid the engine does defensive
  static analysis under the institution's AUP. Exploit or proof-of-concept generation is off-grid,
  out of scope for this repository and the grid by design; we build the boundary, not the weapon.
  Target ingestion is restricted to open-source or documented, authorized bug-bounty scopes.
- **The Beethoven name and the trademark sentence** (above) stand wherever the product is described.
  Never use the p-word (the one that starts "prov" and ends "enance") for the history of a file or a
  piece of data; say origin, source, lineage or record. Versions are integers (1.0.0), never
  fractional.

## Definition of done — every module that enters a run (Goal 1)

No module is "done", and the gate will not let it enter a pipeline, until it clears all of:

1. **Deterministic environment.** It resolves inside `pixi.lock` (exact versions, build strings,
   hashes, `linux-64`) and runs inside the Apptainer image built `--fakeroot` and run
   `--containall --cleanenv --no-home`.
2. **Design by Contract.** `icontract` `@require`/`@ensure`/invariants on every public function;
   subclasses only weaken preconditions and strengthen postconditions (LSP preserved).
3. **Layered runtime types.** `pydantic` at external boundaries, `beartype` on internal hot paths,
   `typeguard` in test enclaves.
4. **Symbolic proof.** `CrossHair` + Z3 find no input that satisfies a precondition and violates a
   postcondition, within the declared per-function bound. Path explosion is bounded (concrete loop
   bounds, I/O concretized at the edges, pure logic verified symbolically).
5. **Schema-aligned parsing.** Any LLM-structured output goes through BAML/SAP-style coercive repair
   with validation feedback, never a hard crash on a minor formatting slip.
6. **SpecPylot loop.** Generate code + contracts → compile → CrossHair → feed a Z3 counterexample
   back → iterate until proven. Humans review the **contracts**, not raw lines.

`src/beethoven_research/example.py` is the worked example that clears 1–4 today, so the gate is
demonstrably real and not a stub. `tools/verify_gate.py` is step 4 across the package; `pixi run
gate` runs every lane; CI runs the same. Steps 5 and 6, and the verification *library* that packages
this for pipeline code, are G1 issues still to build.

## Token economy — always on, every run (Goal 3 / Goal 5 orchestration half)

Every name below was checked against code.claude.com/docs on 2026-10-04 (three names in the plan
were stale; the plan's §4 is the intent, this is what the tool actually accepts). Two places hold
the knobs, and which one is not a matter of taste: an `env` value in the image's managed settings
overwrites the same variable a job exports, so the image holds only what is never tuned
(`containers/managed-settings.json`, `containers/README.md`), and the job template holds the rest.

- **Lazy tool loading** is Claude Code's default: with `ENABLE_TOOL_SEARCH` unset, MCP tool schemas
  are deferred and fetched on demand. Nothing to set; the variable matters only behind a proxy.
  The `mcp-cli` route in the plan is not needed for this.
- **Prompt-cache TTL**: on a Claude subscription, within the plan's included usage (decision D1),
  the main `-p` conversation already gets the one-hour TTL and everything else (subagents,
  compaction, titles) five minutes; nothing is set. Claude Code's drop to five minutes, and the
  per-write billing of an hour kept with `promptCacheTtl` or `CLAUDE_CODE_PROMPT_CACHE_TTL`, apply
  only to a run on usage credits, which the engine never is (the non-negotiable above), so neither
  is set and neither is a pacing knob (Linear BEE-118).
- **Auto-compaction**, job-side per step: `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (a plain integer from
  100000 to 1000000, capped at the model's window) with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (1–100;
  it can only lower the threshold, and applies only in sessions that compact before the model's
  context limit, which is why the two go together) (Linear BEE-119). Dual-hook state preservation
  (PreCompact serialises decisions to node-local scratch, SessionStart with the `compact` matcher
  re-injects them through `hookSpecificOutput.additionalContext`) is BEE-120, built with the run
  directory it writes into; hooks of the engine's own are baked into the image like the guard,
  since `allowManagedHooksOnly` is on there.
- **Concurrency**: `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1` is locked in the image (no nested
  subagent trees); `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (default 20) is set by the job to the
  allocation's cores (Linear BEE-133). `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` has been a no-op
  since Claude Code 2.1.224 and is not used. Fan-out from inside a step is also capped by the guard
  (`sbatch --array` over 200 tasks is refused); larger fan-out is the orchestrator's, paced.
  `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (1 to 256, Claude Code 2.1.269 or later; the default
  is 16, fewer in a CPU-limited container) caps a dynamic workflow's agents the same way and is also
  the job's, set to the allocation's cores.
- **Effort**: `CLAUDE_CODE_EFFORT_LEVEL` takes `low`, `medium`, `high`, `xhigh`, `max` or `auto`;
  `medium` for implementation steps, higher for planning steps, set by the job. `ultracode`, which
  the plan names, is not an effort value but a switch beside it (checked 2026-10-04 against the
  workflows page): `/effort ultracode` or `claude --effort ultracode` (which also sets `xhigh`) in
  an interactive session, or the `ultracode` settings key; with it on, Claude plans a dynamic
  workflow for every substantive task. The `ultracode` keyword in a prompt starts nothing when the
  prompt arrives through `-p`; whether the settings key makes a `-p` step plan workflows is not yet
  measured. Workflows and worktrees are in "Parallel work" below.
- **The shape of a `-p` step** (greg's Playbook, checked against the headless-mode page): the first
  window starts the session with `--session-id`, every later window resumes it with `--resume` in
  the same `CLAUDE_CONFIG_DIR`, so a step re-enters with its own compacted context instead of
  re-reading the repository; `--permission-mode dontAsk` turns every would-be prompt into a
  denial the model can see; `--max-turns` bounds the window; `--output-format stream-json
  --verbose` makes every event a JSON line, and the final `result` event carries `subtype`,
  `is_error`, `num_turns`, `permission_denials` and `total_cost_usd` (the cost is a list-price
  figure; on a subscription it is not a bill). A usage-limit hit (`system/api_retry` with
  `error: rate_limit`, or a result that says so) is a *scheduled* resubmission after the window
  rolls, never a retry loop that burns the next window too. A step that may run a dynamic workflow
  or take messages from a sibling session on the same node carries `--settings` with
  `permissions.allow: ["Workflow"]` and `crossSessionInbound: "accept"`: in `-p` the Workflow
  tool goes through permission evaluation like any tool, so `dontAsk` would deny it, and a `-p`
  session holds a sibling's message only five minutes without that setting (both from the pages,
  2026-10-04). The usage-limit pause of an interactive workflow does not exist in `-p`: the
  affected agent fails, and the pacing above owns the retry.
- **Quota-window-aware pacing** by the orchestrator: hold a step or a `foreach` branch when the
  window is nearly spent, resume when it rolls, hibernate rather than retry. How Claude Code
  reports the remaining window is **not yet measured** (Linear BEE-121 measures it first); nothing
  here should claim a number for it.

## Accounts and sign-in (D4)

The whole of it is `docs/ACCOUNTS.md` and BUILD_PLAN §17.2.1; the rule in four lines:

- One sign-in directory per account: `$HOME/.beethoven/accounts/<label>/claude`, `<label>` the
  student's own word (lower-case letters, digits and hyphens). The launcher's own directory,
  `$HOME/.beethoven/claude` (the profile's `claude_config_dir`), is the account `main`, reached by a
  link; nothing is moved. `accounts.json` lists label, path, enabled and order, and nothing from
  inside any directory.
- One sitting per account, once: `bee account add <label>` (engine, Linear BEE-185) or
  `hpcworker claude login --account <label>` (framework), both the launcher's one sitting on a
  compute node under that `CLAUDE_CONFIG_DIR` (`claude auth login`, paste the code; then
  `claude remote-control` once when the account will run a master with Remote Control). Neither is
  built yet; until then the sitting is typed as `docs/ACCOUNTS.md` shows. No sign-out is ever
  needed: "use another account" is "add an account".
- A job binds exactly one account's directory at `/claude` (the bind in `containers/README.md`),
  the orchestrator chooses which, and `claude auth status` is the only read; a directory that is not
  signed in is skipped and reported, never repaired. Nothing of ours reads, copies, moves or links
  anything inside those directories.
- The super-master lease is one row in the engine store, taken by compare-and-set with a fencing
  token and renewed every 60 s; the hand-off writes the top-level state, names the successor and
  releases, and the successor's job, on its own account's directory, reads the state and continues.
  The successor's job also binds the holder's session transcript, one file, read-only, and starts
  Claude Code with `--resume <that path> --fork-session`, so the conversation continues under the
  next account with a new session id; the path comes from the spent session's own `SessionStart`
  hook, which records it in the run directory (greg, 03:23Z; not built until probe M13 is on
  record, `docs/ACCOUNTS.md` section 9). Every switch is logged. The usage threshold (90 % by default, claude-swap's number) waits on how
  Claude Code reports the window, not yet measured (BEE-121, BEE-188).

## Parallel work: worktrees, workflows, teams and messaging

Read `docs/PARALLEL.md` before writing anything that runs in parallel; every fact there was checked
against the worktrees, workflows, agent-teams, cross-session-messaging and agents pages on
2026-10-04 (greg's asks of 02:30Z and 02:37Z). What binds code here:

- **Worktrees isolate files, one per parallel editor.** A build stream is a subagent with
  `isolation: worktree` (`.claude/agents/stream.md`), branched from the current HEAD
  (`worktree.baseRef: "head"` in `.claude/settings.json`), with `pixi install --locked` as its first
  step because `.pixi/` is not carried into a fresh checkout; `.claude/worktrees/` is ignored by git.
  Inside the engine a step's subagents may use worktrees of the job's own clone; jobs never share a
  checkout: each Slurm job clones onto node-local scratch and pushes its branch, because git on a
  shared filesystem under writers on several nodes is not something git promises.
- **Dynamic workflows hold a plan in a script, not in a context window.** Repeatable orchestration
  lives in `.claude/workflows/<name>.js` (a literal `meta` first; `agent`, `pipeline`, `parallel`,
  `phase`, `log`, `args`; no `Date.now()`, `Math.random()` or `new Date()`, which throw so that a run
  can be resumed) and runs as `/<name>`. Fan-out inside a workflow is capped by
  `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS`, 1000 agents per run and 4096 items per call; a
  silent cap is never written, a dropped item is logged.
- **Agent teams need an interactive session.** `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` makes a
  named subagent a teammate in an interactive session; in `-p` no teammate is ever spawned, a
  session has one team, a teammate cannot spawn a team, and the lead is fixed. So (BUILD_PLAN
  §17.2.2 and §17.2.3) a **master** is an interactive Claude Code session under a pseudo-terminal in
  its Slurm job, that account's one team lead, with in-process teammates only, never tmux; a `-p`
  step never leads a team; a **satellite** is a new Slurm job a master asks for through the engine's
  own tool, registered before its first turn, under the same account; a satellite that leads a team
  of its own is the team of teams, and a team whose members belong to several accounts exists only
  at the engine layer, joined by the task graph and the bus. Whether a session under a
  pseudo-terminal driven only through its inbox socket spawns teammates is not yet measured (§17.4
  M1).
- **Messages are plain text and never consent.** On one node: Claude Code's own mailbox inside a
  team and the per-session socket (`/tmp/cc-socks-<uid>`, on the node-local `/tmp` the job binds);
  a session in a container and one on the host, or sessions on two nodes, do not reach each other
  over it. Across nodes and accounts: the engine's bus (the store's `messages` and `registry`
  tables, an append-only inbox file per session on the lab volume as the fallback), carried into
  each session by a sidecar that posts into its inbox socket with the session's own token (Claude
  Code exports `CLAUDE_CODE_MESSAGING_SOCKET` and `CLAUDE_CODE_MESSAGING_TOKEN` only to the session's
  own hooks and Bash commands, before `SessionStart` runs, so a `SessionStart` hook baked into the
  image writes both to a mode-0600 file in the run directory for the sidecar; the token is a secret
  under the secrets rule; the managed settings never set `crossSessionInbound`, since a `hold` there
  would override every job's `accept`); across nodes for one account, Remote Control carries
  notices. A message from another session cannot
  approve anything, change settings or run a command; every message is logged; nothing of ours asks
  another session to do what its own session was refused.
- **No orphans** (§17.2.6). One registry row per session (run, account, job id, node, session id,
  parent, role, heartbeat, state), written before the session's first turn; a lead shuts its
  teammates down before it exits; a master's exit path tells its satellites to finish and then
  cancels, through the adapter, only the job ids the registry says are its own; a satellite whose
  parent's heartbeat lapses asks the super-master for adoption; the reaper reconciles the registry
  against one `squeue` per 60 s floor; a session the reaper closed is an INTERVENTIONS failure row.
  `-p` leaves worktrees and their locks behind, so a step that made one removes it by name before
  the job ends (its own run artefact, never another's).

## The engine is itself the subject of a methods paper (added 2026-10-04)

The build and every run are measured so the whole thing can be written up as a protocol / methods
paper whose contribution is a relatively undirected agent on a shared academic grid (the prior-work note under the project's plan folder, pending its fold into
BUILD_PLAN.md; greg's message of 2026-10-04). Three things follow for code written here:

- **Every long-running step declares its progress signal.** Anthropic's "Long-running Claude for
  Scientific Computing" (2026-03-23) puts it plainly: long autonomous scientific work depends on the
  agent having some way to tell whether it is making progress — a reference implementation, a
  clearly quantifiable objective, or an existing test suite — and on committing after each
  meaningful unit of work. A `claude -p` step without a declared signal is not scheduled (Linear
  G5-7, BEE-178); the peeking Claude and the run index read that signal, never guess from logs.
- **Human interventions are first-class data.** When a person steps in — to restart a stuck step,
  redefine a search space, correct a result — it is logged then and there (what the agent was
  doing, why, what changed, outcome), never reconstructed afterwards (Linear M-5, BEE-174). Prior work
  found such interventions frequent; their count is a headline number of the paper, so it must be
  honest.
- **Each run records its compute and quota spend** beside its Run ID: Slurm job ids, partition and
  QoS, wall time requested and used, GPU-hours, preemptions, tokens in and out, cache hit-rate,
  compaction events, subagent count, energy (Linear G5-8, BEE-179).

Prompts, instruction files (this one included), agent logs, job scripts and git history are the
paper's record; keep them whole and never trim them for tidiness. The cluster's acceptable-use
position on autonomous agents and the paper's AI-use disclosure are the maintainer's hand step
(Linear M-8, BEE-177) and gate the first unattended run.

## Build and test

```sh
pixi install --locked     # reconstruct the exact env from the lock; CI uses --locked too
pixi run gate             # lint (ruff) + typecheck (mypy) + tests (pytest) + symbolic (CrossHair/Z3)
pixi run symbolic         # just the gate
```

A Claude Code cloud session gets the same environment from `.claude/hooks/session-start.sh`
(installs pixi 0.81.0 and runs `pixi install --locked`; cloud sessions only, idempotent). That hook
runs in a session with one repository; a project thread with several repositories does not read it
and needs the same commands as the environment's setup script.

**The gate runs before every push** (greg, 2026-10-04): `.githooks/pre-push` runs `pixi run gate`
and a red gate does not leave the machine; `pixi run hooks` (or the session-start hook) points git
at it with `core.hooksPath`. `SKIP_GATE=1` skips it knowingly, and the commit says why. On GitHub
the same gate is the required check on `main`: the rule lives in `.github/rulesets/main.json`
(deletion and force-push blocked; the `gate` and `image` lanes must be green; organization admins
bypass) and is imported into the repository's rulesets, so whether a pull request lands is decided
by the tests, with no review step and no human click once auto-merge is armed on it. Pull requests
are opened ready for review, never as drafts.

Anything that runs a container, a scheduler call or Claude Code runs on the grid inside a Slurm job,
never on the login node and never on a laptop. The Apptainer image (`containers/claude-verify.def`)
is built with `containers/build.sh`, which records what was built in `containers/claude-verify.digest`;
the `.sif` itself is not committed (it is large and rebuilt from the recipe + lock). The image
bakes in `containers/managed-settings.json` and the guard; `containers/README.md` says what is
locked there and what a job sets per step.

## Layout

- `pixi.toml` / `pixi.lock` — the substrate: the verification stack (Goal 1) and the dev tooling the
  gate runs, pinned for `linux-64`. Orchestration (Metaflow), medical (pydicom/MONAI) and telemetry
  (W&B/CodeCarbon) are added as their own pixi features with their own issues, so this lock stays
  small and honest rather than pinning an engine that does not exist yet. The first research
  target the engine will run is the spine project (intraoperative fluoroscopic vertebral-body
  detection and counting, synthetic DRRs, domain adaptation; greg's Drive folder *spine*): it is
  what the medical feature is sized for when Goal 6 starts, and its plan side (sources, PHI, where
  the data lives) is the planning thread's and Linear BEE-165's, not this file's.
- `containers/` — the Apptainer recipe (Node + Claude Code CLI + this pixi env + the managed
  settings and the guard), the build script and the recorded digest.
- `src/beethoven_research/` — the engine package: `contracts.py` (the DbC conventions),
  `example.py` (the worked, fully-verified example) and `guard.py` (the PreToolUse guard the image
  runs before every shell and file call).
- `tools/verify_gate.py` — the CrossHair/Z3 gate.
- `docs/` — `ACCOUNTS.md` (the sign-in path and the account list, D4) and `PARALLEL.md` (worktrees,
  workflows, teams and messaging: how the build and the engine run in parallel).
- `.claude/agents/stream.md` — the build stream, a subagent that always works in its own worktree;
  `.claude/workflows/` — the saved dynamic workflows (`gate-until-green`, `verify-module`), run as
  `/<name>` from any session in this checkout.
- `tests/` — pytest, imported under the typeguard hook.
- `.github/workflows/ci.yml` — the CI lanes; `.mcp.json` — Linear + GitHub via the environment.

## How to be useful here

The recurring failure mode across these projects is a number or a state that is wrong but plausible
— a fabricated hash, a lock that was hand-edited, a "passed" that never ran. Check against something
outside the computation: regenerate the lock and diff it, build the image and read back its digest,
run the gate and read its counterexample. Never commit a lockfile or a digest you did not produce
with the tool. Say plainly what is not known; "not yet measured" is the preferred form.

Every unit of build work appends one row to `/mnt/project-files/ledger/LEDGER.md` (format in
`ledger/README.md`): date, thread, actor, activity, wall-minutes, bugs fixed, inputs, outputs,
how it was verified. Re-read the file just before appending — other threads write it too.
