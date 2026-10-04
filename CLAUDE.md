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

- **D1 — own subscription, no keys.** Every `claude -p` step draws on the **student's own** flat-rate
  Claude plan, the same rolling 5-hour window their manual sessions use. No pooled or commercial API
  keys, no LiteLLM gateway, no Bedrock/Vertex, no key rotation. Goal 3 is quota-window-aware pacing,
  not routing.
- **D2 — the engine is this repository.** The launcher stays in its two repositories.
- **D3 — Metaflow + `outerbounds/metaflow-slurm`**, with a plain `sbatch` adapter underneath first.

## Non-negotiables (carried from the launcher; they bind the engine too)

- **Never touch a Claude credential.** No `setup-token`, no `CLAUDE_CODE_OAUTH_TOKEN`, no reading
  `~/.claude/.credentials.json` or the student's `claude_config_dir` credentials. The engine starts
  Claude Code where the student's own sign-in already is and reads only `claude auth status`. One
  person, one account.
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
  compaction, titles) five minutes; nothing is set. Once a run draws on usage credits Claude Code
  drops the main conversation to five minutes by its own rule, and keeping the hour
  (`promptCacheTtl: "1h"` or `CLAUDE_CODE_PROMPT_CACHE_TTL=1h`) is then billed per write; whether
  to do that is a BEE-121 pacing decision, never a default (Linear BEE-118).
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
- **Effort**: `CLAUDE_CODE_EFFORT_LEVEL` takes `low`, `medium`, `high`, `xhigh`, `max` or `auto`;
  `medium` for implementation steps, higher for planning steps, set by the job. `ultracode`, which
  the plan names, is not one of these values and is not yet checked.
- **The shape of a `-p` step** (greg's Playbook, checked against the headless-mode page): the first
  window starts the session with `--session-id`, every later window resumes it with `--resume` in
  the same `CLAUDE_CONFIG_DIR`, so a step re-enters with its own compacted context instead of
  re-reading the repository; `--permission-mode dontAsk` turns every would-be prompt into a
  denial the model can see; `--max-turns` bounds the window; `--output-format stream-json
  --verbose` makes every event a JSON line, and the final `result` event carries `subtype`,
  `is_error`, `num_turns`, `permission_denials` and `total_cost_usd` (the cost is a list-price
  figure; on a subscription it is not a bill). A usage-limit hit (`system/api_retry` with
  `error: rate_limit`, or a result that says so) is a *scheduled* resubmission after the window
  rolls, never a retry loop that burns the next window too.
- **Quota-window-aware pacing** by the orchestrator: hold a step or a `foreach` branch when the
  window is nearly spent, resume when it rolls, hibernate rather than retry. How Claude Code
  reports the remaining window is **not yet measured** (Linear BEE-121 measures it first); nothing
  here should claim a number for it.

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
