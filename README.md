# beethoven-research

The **autonomous prompt-to-paper research engine** — the compute half of the Beethoven project. It
is a *peer* to the Beethoven launcher (`hpcworker` + `beethoven-extension`), not a replacement: the
launcher is the student's interactive Remote Control worker; this engine is the headless
`claude -p` research loop that runs as Metaflow-orchestrated Slurm jobs inside the same hermetic
Apptainer image, under the student's **own** Claude subscriptions (one or several, each in the
student's own name).

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs; "Open OnDemand" is the mark of its publisher.

## Status

Scaffolding. This repository currently contains the **deterministic substrate** (Pixi manifest +
lockfile, Apptainer recipe), the **verification gate** (one worked example that clears the full
definition-of-done, plus the CI lanes that enforce it) and the **working context** (`CLAUDE.md`).
The research modules themselves (the verification library, orchestration spine, REFORMS layer,
medical calibration, publication engine) are tracked in Linear (team *Beethoven*, projects P0 and
G1–G7) and land as reviewed pull requests. See `/mnt/project-files/plan/BUILD_PLAN.md` in the
project for the full plan.

## What is settled (do not reopen — decided by the maintainer 2026-10-03 and 2026-10-04)

- **D1 — the engine runs on each student's own flat-rate Claude subscriptions.** No pooled or
  commercial API keys, no LiteLLM gateway, no token or credential handling anywhere in a job. The
  engine starts Claude Code only where one of the student's own sign-ins already is and reads
  `claude auth status`; it never reads, copies, prints or relays a credential. Goal 3's token
  economy is therefore **quota-window-aware pacing** within each account's rolling window, not key
  rotation.
- **D2 — the engine lives in this repository.** `hpcworker` and `beethoven-extension` stay the
  launcher.
- **D3 — orchestration is Metaflow with `metaflow-slurm`** (plain `sbatch` adapter first).
- **D4 — several accounts of one person (2026-10-04).** A student who holds several subscriptions
  in their own name runs them all at once, one sign-in directory and one master job per account,
  with one account holding the super-master role at a time and handing it over as its window nears
  its limit. The hand-off selects which sign-in directory the next job binds; no credential is ever
  copied or swapped (that is where the engine parts from claude-swap). See `docs/ACCOUNTS.md`.
  The switch itself is built: `bee` (below).

These carry over `hpcworker`'s non-negotiables unchanged: no code on claude.ai and no interception
of it; no `CLAUDE_CODE_OAUTH_TOKEN` or `setup-token` in jobs; secrets never on a command line, in
argv, in logs or in `sbatch --export`; nothing is deleted outside the engine's own run directories
and node-local temp; grid etiquette (no container work on the login node, the 60 s client-side
polling floor, a wall time on every job). `CLAUDE.md` is the authority.

## Definition of done (every module that enters a run)

Goal 1, from the plan's code-quality standard. A module is not "done" until it clears, in CI and in
the pipeline gate:

1. a deterministic environment — inside this Pixi lock and the hashed Apptainer image;
2. Design by Contract (`icontract` `@require`/`@ensure`/invariants);
3. layered runtime types — `pydantic` at boundaries, `beartype` on hot paths, `typeguard` in tests;
4. symbolic proof — `CrossHair` + Z3 find no input that satisfies a precondition and violates a
   postcondition, within the declared bound;
5. schema-aligned parsing (BAML/SAP) for any LLM-structured output — coercive repair, never a hard
   crash on a minor slip.

`tools/verify_gate.py` runs step 4 across the package; `pixi run gate` runs all the lanes.

## Build and test

```sh
pixi install --locked        # reconstruct the exact environment from pixi.lock
pixi run gate                # lint + typecheck + tests + the symbolic verification gate
pixi run symbolic            # just the CrossHair/Z3 gate
```

## Switching accounts: `bee`

`bee` lists the student's own Claude accounts, one sign-in directory each, and moves a piece of work
from one to the next with the State Management guide's hand-off file, the same way on a laptop
(Windows, macOS, Linux) and on a compute node inside the image (`bee-grid.sh`). Setup and daily use:
`docs/bee/RUNBOOK.md`; what was taken from the guide and what was changed: `docs/bee/STATE_HANDOFF.md`.

```sh
bee account add work         # Claude Code's own sign-in, once, into ~/.beethoven/accounts/work/claude
bee run work                 # Claude Code on that account, with the hand-off hooks and status line
bee continue                 # after a usage limit or /handoff: the next account picks up the work
```

The Apptainer image is built and run on the cluster, never on the login node:

```sh
bash containers/build.sh                 # apptainer build --fakeroot; records the digest
# runtime flags are always: --containall --cleanenv --no-home   (see containers/README.md)
```

## Layout

```
pixi.toml / pixi.lock      the deterministic substrate (conda-forge + PyPI; linux-64 runs, three laptop platforms for bee)
containers/                the Apptainer recipe, build script and recorded digest
src/beethoven_research/     the engine package (contracts conventions, one worked example, the guard)
src/beethoven_research/bee/ bee: accounts, the hand-off hooks, .claudeignore, the grid script (self-contained)
tools/verify_gate.py        the CrossHair/Z3 contract gate CI calls
tests/                      pytest, run under typeguard
docs/                      ACCOUNTS.md (the sign-in path, D4); PARALLEL.md (worktrees, workflows, teams, messaging); bee/
.claude/agents/             the build stream: a subagent that always works in its own worktree
.claude/workflows/          saved dynamic workflows: gate-until-green, verify-module
.github/workflows/ci.yml    the CI lanes that enforce the definition of done
.mcp.json                   Linear + GitHub MCP, configured from the environment (never hardcoded)
CLAUDE.md                   the working context and the non-negotiables
```

Every unit of build work is recorded in the project ledger
(`/mnt/project-files/ledger/LEDGER.md`) so the whole build is reproducible for the research report.
