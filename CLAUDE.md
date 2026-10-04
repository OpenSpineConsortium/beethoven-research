# beethoven-research — working context

This repository is the **research pipeline** of the Beethoven engine: what is computed. REFORMS
(the reproducibility layer: the lock, run hashing, the clean-tree gate, the split level per corpus,
bootstrapped confidence intervals, energy), CLAIM (the reporting checklist of the first study), the
studies themselves (the spine study first, BUILD_PLAN §16), and later the medical calibration
(Goal 6) and the publication engine behind a human click (Goal 7). It is one of the three
repositories of greg's split of 2026-10-04 (04:52Z; `/mnt/project-files/plan/REPO_SPLIT.md`):
`beethoven-qa` (how code is written and proven) <- `beethoven-agents` (how agents run on the grid:
the image, the guard, accounts and the super-master lease, the `claude -p` loop) <- this one, the
only direction of dependence. It takes beethoven-qa as a pinned copy at `vendor/beethoven-qa/`; a
study runs inside beethoven-agents' image and orchestration, and this package imports nothing of
it yet. The engine as a whole turns a research prompt into a verified, reproducible, archived
result and is the compute **peer** of the Beethoven launcher (`OpenSpineConsortium/hpcworker` +
`OpenSpineConsortium/beethoven-extension`), which is the student's interactive Remote Control
worker. Until the split this repository held the whole engine; "Where the rest went" below says
where each part is now.

Vendor: Gregory Schwing, trading as OpenSpineConsortium. Beethoven runs Claude Code and is not an
Anthropic product; "Claude" and "Claude Code" are Anthropic's marks, used here to say what it runs;
"Open OnDemand" is the mark of its publisher.

## Read first

- `/mnt/project-files/plan/BUILD_PLAN.md` (§3 the definition of done, §2 the conflicts that are
  excluded, §16 the first study) and `plan/SPINE_FIRST_STUDY.md` (the corpora, the REFORMS needs
  in E.2, the G2 items); `plan/REPO_SPLIT.md` for the boundary between the three repositories.
  The plan governs; this file is its standing summary for anyone working in the repo.
- `vendor/beethoven-qa/` (the pinned copy; its `ORIGIN` names the release): the definition of done,
  the gate and the plugin's skills (`vendor/beethoven-qa/plugin/skills/`). beethoven-qa's
  `docs/CONSUMING.md` is how this repository takes it and updates it.
- `hpcworker`'s `CLAUDE.md` for the grid facts and the launcher's non-negotiables, which hold here;
  `beethoven-agents`' `CLAUDE.md` for the token economy, accounts and parallel work a study runs
  under.
- The Linear workspace (team *Beethoven*): G2, G6 and G7 are this repository's goals; S is the
  first study.

## Decisions that are settled (do not reopen)

- **D1 — own subscriptions, no keys.** Every `claude -p` step draws on a flat-rate Claude plan held
  in the **student's own name**, the same rolling 5-hour window their manual sessions use. No pooled
  or commercial API keys, no gateway, no Bedrock/Vertex, no key rotation. "No API key anywhere" is,
  in greg's words (2026-10-04, 13:27Z), the only fundamental rule of this software.
- **D2 — three repositories** (greg, 2026-10-04 04:52Z; his card answer of 13:19Z keeps the
  research pipeline here). The gate, the contracts and the code-writing workflows are
  beethoven-qa's; the image, the guard, accounts and orchestration are beethoven-agents'. A change
  here that needs anything of the launcher's is in the wrong repository.
- **D3 — Metaflow + `outerbounds/metaflow-slurm`**, with a plain `sbatch` adapter underneath first
  (built in beethoven-agents).
- **D4 — several accounts of one person; one master per account; one super-master lease at a
  time** (greg, 2026-10-04). The whole of it is beethoven-agents' (`docs/ACCOUNTS.md` there); a
  study never chooses a sign-in directory itself.
- **The first study** is vertebral body detection on real fluoroscopy under domain shift, with
  corpora already on the grid (BUILD_PLAN §16, `plan/SPINE_FIRST_STUDY.md`). Its twelve open
  decisions are greg's (Linear BEE-181), three of them before the first run; the corpus inventory's
  questions (`plan/IMAGING_SOURCES.md`, BEE-165) are his too.

## Non-negotiables (carried from the launcher and the engine; they bind here too)

- **Never touch a Claude credential.** No `setup-token`, no `CLAUDE_CODE_OAUTH_TOKEN`, no reading
  `~/.claude/.credentials.json` or the credentials in any sign-in directory. A study runs where one
  of the student's own sign-ins already is and reads only `claude auth status`. Nothing of ours
  sets `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`: under greg's rule above the VS Code edition's
  paid overflow, which used one, was removed as well (Linear BEE-191), so no edition of Beethoven
  holds a key.
- **Usage credits are never used.** greg's rule of 2026-10-04 (BUILD_PLAN §2 C7), with the same
  rank as every other bullet here: no run draws on, buys or turns on usage credits for any account,
  and nothing in this repository treats them as an option or a default. A spent window is waited
  out until it resets (beethoven-agents' pacing), never bought through.
- **No code on claude.ai and no interception of it.** Settled for the whole project; the notebook
  analyses that proposed it are excluded (BUILD_PLAN §2, C1).
- **Secrets never appear on a command line, in argv, in logs, in job files or in `sbatch --export`.**
  Never echo one. The `.mcp.json` here reads tokens from the environment and hardcodes none.
- **No remote code.** No `eval`, no `new Function`, no fetched-and-run scripts.
- **Nothing is deleted outside a run's own directories and node-local temp.** Never the Claude Code
  login directory, the workspace, another run, a corpus, or anything else under `$HOME`. A
  destructive subcommand must refuse when `CLAUDECODE`, `BEETHOVEN_REQUEST_ID` (or the older
  `ASHLEY_REQUEST_ID`) or a similar agent marker is in the environment, or when stdin is not a
  terminal (the lesson of the 2026-09-28 `~/.ashley` deletion). The imaging migration copies,
  verifies and locks; it never deletes (`plan/IMAGING_MIGRATION_RUNBOOK.md`).
- **PHI stays on the grid** (BUILD_PLAN §16). Identifying fields exist only where greg's PHI
  statement allows them (BEE-165), and nothing that identifies a patient goes into a context window,
  a log, the ledger, Linear, a pull request or a figure; a step reads logs and result files, never
  streams images into its context. Before any corpus is read, its row in the study record carries an
  access and licence statement and an IRB or determination status; "not stated" blocks a clinical
  corpus and allows a public synthetic one (BUILD_PLAN's G4 row).
- **No public push, release, preprint or submission from the engine.** The Publish click, and the
  Invention Disclosure gate before it, are greg's (BUILD_PLAN's G4 row; Goal 7 lands behind that
  click).
- **Grid etiquette.** Nothing container-related on the login node; container temp and cache on the
  compute node's local disk (`/tmp/$USER/...` with a cleanup trap — on Wayne State `$TMPDIR` is NOT
  node-local, it is CephFS, so never trust it blindly); IPC under `/dev/shm`; client-side scheduler
  polling never faster than 60 s (read the status file, do not re-`squeue`); every job has a wall
  time; training, rendering and evaluation are Slurm jobs, one `sbatch` each. Attribute these
  exactly when writing for anyone outside (see `hpcworker/CLAUDE.md`).
- **The Beethoven name and the trademark sentence** (above) stand wherever the product is described.
  Never use the p-word (the one that starts "prov" and ends "enance") for the history of a file or a
  piece of data; say origin, source, lineage or record. Versions are integers (1.0.0), never
  fractional.

## Definition of done — every module that enters a run (Goal 1)

beethoven-qa owns it (its `CLAUDE.md` and the `definition-of-done` skill, pinned here under
`vendor/beethoven-qa/`); this repository runs its gate on its own package. In short, no module is
"done", and the gate will not let it enter a pipeline, until it clears all of:

1. **Deterministic environment.** It resolves inside `pixi.lock` (exact versions, build strings,
   hashes, `linux-64`) and runs inside the hashed Apptainer image: beethoven-agents' substrate
   image, and the study image layered on it once Goal 6 starts (`plan/REPO_SPLIT.md` section 2).
2. **Design by Contract.** `icontract` `@require`/`@ensure`/invariants on every public function,
   imported from `beethoven_qa.contracts`; `@beartype` outermost, the contracts beneath it.
3. **Layered runtime types.** `pydantic` at external boundaries (the study record, a corpus's
   metadata, LLM output), `beartype` on internal hot paths, `typeguard` in the test enclave
   (`tests/conftest.py`).
4. **Symbolic proof.** CrossHair + Z3 find no input that satisfies a precondition and violates a
   postcondition, within the declared per-condition bound (20 s, `pyproject.toml`).
5. **Schema-aligned parsing** for any LLM-structured output: coercive repair with validation
   feedback, never a hard crash (lands in beethoven-qa, Linear G1-4).
6. **The SpecPylot loop.** Code and contracts, the gate, the counterexample fed back, again until
   proven; humans review the contracts, not the lines (`vendor/beethoven-qa/plugin/skills/`).

`python -m beethoven_qa gate` is steps 2 and 4 across `beethoven_research` (the table
`[tool.beethoven-qa]` in `pyproject.toml`). `pixi run --locked gate` runs every lane,
`vendor-check` among them; CI runs the same. A function CrossHair must not run (it reads, writes,
asks the clock or the environment, starts a process) is named under `[tool.beethoven-qa.unproven]`
with its reason, which the gate prints on every run; a `# crosshair:` comment fails the gate; a
new entry is a person's decision in a reviewed change, never a way to get green, and every
contracted function that calls a listed one is listed too, since CrossHair runs the listed body
when it proves the caller. The list is empty today: the first module is pure.

## REFORMS and the first module (Goal 2)

- **The split level per corpus** (patient, study, cadaver or CT source volume) is written in the
  study record before the first training job, and a test fails if any evaluation case shares a
  patient, cadaver or source volume with a training case (BUILD_PLAN's G2 row;
  `plan/SPINE_FIRST_STUDY.md` E.2 and G2 item 1). `src/beethoven_research/splits.py` is that check,
  pure and gated: `SplitLevel` names the level, `Split` is the boundary where the record's two
  partitions are read (pydantic), `shared_groups` and `disjoint` decide under beartype and the
  contracts. The groups come from the record, never from a corpus the module reads itself.
  CTSpinoPelvic1K is out of any split until its identity audit closes.
- **Every result row** carries n, a 95 % confidence interval, the Run ID, the commit and the
  clean-tree flag; a row from a dirty tree is refused. Not built yet (Linear G2).
- **Every reported number** is reproduced by one command that exits non-zero if it moved (the
  `make physics` model of `spinesurg-ct-nnunet`). Not built yet (Linear G1).

## The engine is itself the subject of a methods paper

The build and every run are measured so the whole thing can be written up as a protocol / methods
paper whose contribution is a relatively undirected agent on a shared academic grid (greg's message
of 2026-10-04; the prior-work note under the project's plan folder). Three things follow for code
written here:

- **Every long-running step declares its progress signal.** Anthropic's "Long-running Claude for
  Scientific Computing" (2026-03-23) puts it plainly: long autonomous scientific work depends on the
  agent having some way to tell whether it is making progress — a reference implementation, a
  clearly quantifiable objective, or an existing test suite — and on committing after each
  meaningful unit of work. A `claude -p` step without a declared signal is not scheduled (Linear
  G5-7, BEE-178); the peeking Claude and the run index read that signal, never guess from logs.
- **Human interventions are first-class data.** When a person steps in — to restart a stuck step,
  redefine a search space, correct a result — it is logged then and there (what the agent was
  doing, why, what changed, outcome), never reconstructed afterwards (Linear M-5, BEE-174). Prior
  work found such interventions frequent; their count is a headline number of the paper, so it
  must be honest.
- **Each run records its compute and quota spend** beside its Run ID: Slurm job ids, partition and
  QoS, wall time requested and used, GPU-hours, preemptions, tokens in and out, cache hit-rate,
  compaction events, subagent count, energy (Linear G5-8, BEE-179).

Prompts, instruction files (this one included), agent logs, job scripts and git history are the
paper's record; keep them whole and never trim them for tidiness. The cluster's acceptable-use
position on autonomous agents and the paper's AI-use disclosure are the maintainer's hand step
(Linear M-8, BEE-177) and gate the first unattended run.

## Build and test

```sh
pixi install --locked         # reconstruct the exact env from the lock; CI uses --locked too
pixi run --locked gate        # lint + typecheck + tests + symbolic (beethoven-qa's gate) + vendor-check
pixi run --locked symbolic    # just the CrossHair/Z3 gate: python -m beethoven_qa gate
pixi run --locked vendor-check  # the pinned copy still matches its ORIGIN, the kit files equal the copy's
```

The pinned copy and the four kit files (`.githooks/pre-push`, `.github/workflows/automerge.yml`,
`.github/workflows/rulesets.yml`, `.claude/hooks/session-start.sh`) are never edited here; a change
is made in beethoven-qa, released, and taken with its `vendor` command (beethoven-qa
`docs/CONSUMING.md`, "Updating to a new release"). The lock changes only on purpose, by pixi, in a
change that says so (the slimming of 2026-10-04 re-locked for `linux-64` alone, and the `linux-64`
entries did not move).

A Claude Code cloud session gets the same environment from `.claude/hooks/session-start.sh`
(installs pixi 0.81.0 and runs `pixi install --locked`; cloud sessions only, idempotent). That hook
runs in a session with one repository; a project thread with several repositories does not read it
and needs the same commands as the environment's setup script, and it must point git at the hooks
itself (`git config core.hooksPath .githooks`, or `pixi run hooks`) before its first push.

**The gate runs before every push** (greg, 2026-10-04): `.githooks/pre-push` runs
`pixi run --locked gate` and a red gate does not leave the machine. The hook gates the commit that
is pushed: every ref must be the checkout's HEAD and the tree must hold nothing uncommitted
(untracked files count), or it refuses and says so; from a linked worktree it drops the `GIT_DIR`
git hands a hook before the gate runs. `SKIP_GATE=1` skips all of it knowingly, and the commit
says why. On GitHub the same gate is the required check on `main`: the rule lives in
`.github/rulesets/main.json` (deletion and force-push blocked; the `gate` lane must be green, by
its exact job name; organization admins bypass) and is applied by the kit's `rulesets.yml`, so
whether a pull request lands is decided by the tests, with no review step and no human click once
auto-merge is armed on it. Pull requests are opened ready for review, never as drafts.

Anything that runs a container, a scheduler call or Claude Code runs on the grid inside a Slurm job,
never on the login node and never on a laptop. The image is beethoven-agents' (`containers/` there);
the study image that layers the medical and telemetry stacks on it is not built yet.

## Where the rest went (the split, 2026-10-04)

Until the split this repository held the whole engine; its git history up to then is the record
of that work, and new work on those parts goes to their repositories:

- the contracts module, the worked example, the CrossHair/Z3 gate (`tools/verify_gate.py`), the
  build-stream agent and the workflows `gate-until-green` and `verify-module`: **beethoven-qa**
  (`beethoven_qa.contracts`, `beethoven_qa.example`, `python -m beethoven_qa gate`, the plugin),
  pinned here under `vendor/beethoven-qa/`;
- the PreToolUse guard, the Apptainer recipe and the managed settings, `docs/ACCOUNTS.md`,
  `docs/PARALLEL.md`, and bee (`bee account`, the hand-off hooks, the status line, the grid script,
  `docs/bee/`): **beethoven-agents** (`beethoven_agents.guard`, `beethoven_agents.bee`,
  `containers/`, `docs/`).

## Layout

- `pixi.toml` / `pixi.lock` — the verification stack beethoven-qa's gate needs, the same lines as
  beethoven-qa's, pinned for `linux-64` (the three laptop platforms left with bee). The medical
  (pydicom/MONAI, Goal 6) and telemetry (W&B/CodeCarbon, Goal 2) stacks are added as their own
  pixi features with their own Linear issues, each in a change that re-locks on purpose.
- `src/beethoven_research/` — the package: `splits.py` (the split-level check). The REFORMS
  record, the CLAIM checklist, the study code and the publication engine land here.
- `vendor/beethoven-qa/` — the pinned copy of beethoven-qa (`ORIGIN`, the package, the plugin, the
  kit as released); never edited by hand.
- `tests/` — pytest, imported under the typeguard hook.
- `.githooks/`, `.github/`, `.claude/` — the kit, the CI gate lane, the rule as code, session
  settings; `.mcp.json` — Linear + GitHub via the environment.

## How to be useful here

The recurring failure mode across these projects is a number or a state that is wrong but plausible
— a fabricated hash, a lock that was hand-edited, a "passed" that never ran, a split that is
"held-out" at no stated level. Check against something outside the computation: regenerate the
lock and diff it, run the gate and read its counterexample, make a contract fail on purpose before
trusting that it passes. Never commit a lockfile or a digest you did not produce with the tool. Say
plainly what is not known; "not yet measured" is the preferred form.

Every unit of build work appends one row to `/mnt/project-files/ledger/LEDGER.md` (format in
`ledger/README.md`): date, thread, actor, activity, wall-minutes, bugs fixed, inputs, outputs,
how it was verified. Re-read the file just before appending — other threads write it too.
