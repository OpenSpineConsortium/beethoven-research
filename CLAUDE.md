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
  terminal. This is the lesson of the 2026-09-28 `~/.ashley` deletion; the guard hook (G4) enforces
  it and the Z3 gate proves the file-touching helpers cannot escape their run directory.
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

Lazy tool loading (`ENABLE_TOOL_SEARCH` / mcp-cli, schemas on demand); prompt-cache TTLs via managed
settings (`promptCacheTtl: 1h` lead, `subagentPromptCacheTtl: 5m`); preemptive auto-compaction
(`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=65` paired with `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000` — the
override alone is unreliable in `-p`); dual-hook state preservation (PreCompact serializes to
node-local `scratchpad.json`, SessionStart re-injects); concurrency caps
(`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`=cores, `MAX_SUBAGENT_SPAWN_DEPTH=1`,
`MAX_SUBAGENTS_PER_SESSION=50`); quota-window-aware pacing by the orchestrator (hold a step or a
`foreach` branch when the window is nearly spent, resume when it rolls, hibernate rather than
retry). How Claude Code reports the remaining window is **not yet measured** (Linear BEE-121
measures it first); nothing here should claim a number for it.

## Build and test

```sh
pixi install --locked     # reconstruct the exact env from the lock; CI uses --locked too
pixi run gate             # lint (ruff) + typecheck (mypy) + tests (pytest) + symbolic (CrossHair/Z3)
pixi run symbolic         # just the gate
```

Anything that runs a container, a scheduler call or Claude Code runs on the grid inside a Slurm job,
never on the login node and never on a laptop. The Apptainer image (`containers/claude-verify.def`)
is built with `containers/build.sh`, which records the image digest to `containers/*.sif.sha256`;
the `.sif` itself is not committed (it is large and rebuilt from the recipe + lock).

## Layout

- `pixi.toml` / `pixi.lock` — the substrate: the verification stack (Goal 1) and the dev tooling the
  gate runs, pinned for `linux-64`. Orchestration (Metaflow), medical (pydicom/MONAI) and telemetry
  (W&B/CodeCarbon) are added as their own pixi features with their own issues, so this lock stays
  small and honest rather than pinning an engine that does not exist yet.
- `containers/` — the Apptainer recipe (Node + Claude Code CLI + this pixi env), the build script
  and the recorded digest.
- `src/beethoven_research/` — the engine package: `contracts.py` (the DbC conventions) and
  `example.py` (the worked, fully-verified example).
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
