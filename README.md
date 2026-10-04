# beethoven-research

The **research pipeline** of the Beethoven engine: what is computed. REFORMS (the reproducibility
layer), CLAIM (the reporting checklist), the studies (the spine study first) and, later, the medical
calibration and the publication engine behind a human click.

It is one of three repositories: `beethoven-qa` (how code is written and proven), `beethoven-agents`
(how agents run on the grid: the image, the guard, accounts, orchestration) and this one. They
depend in one direction only, qa <- agents <- research. This repository carries a pinned copy of
beethoven-qa at `vendor/beethoven-qa/`; a study runs inside beethoven-agents' image and
orchestration. The engine is the compute peer of the Beethoven launcher (`hpcworker` +
`beethoven-extension`), which is the student's interactive Remote Control worker.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs; "Open OnDemand" is the mark of its publisher.
Vendor: Gregory Schwing, trading as OpenSpineConsortium. Private; the code is proprietary.

## What is settled

- **D1:** every step runs on a flat-rate Claude plan held in the student's own name. No pooled or
  commercial API keys, no gateway, no usage credits, ever; "no API key anywhere" is the one
  fundamental rule of this software. Nothing here reads, copies, prints or relays a credential.
- **D2:** three repositories (2026-10-04); the research pipeline stays here.
- **D3:** orchestration is Metaflow with `metaflow-slurm`, built in beethoven-agents.
- **D4:** several accounts of one person, handled in beethoven-agents; a study never picks a sign-in.
- **PHI stays on the grid**, and no public push, release, preprint or submission ever comes from
  the engine: the Publish click is the maintainer's.

`CLAUDE.md` holds the rest: the launcher's non-negotiables, the definition of done, the REFORMS
needs of the first study and what is measured.

## Status

The first module is in: `src/beethoven_research/splits.py`, the split-level check that REFORMS
asks of the first study (the level at which a corpus's partitions are disjoint is declared before
training, and no evaluation case may share a patient, cadaver or source volume with a training
case). Everything else of the pipeline (the result-row rule, the reproduce-every-number command,
the CLAIM record, the study code, the medical and telemetry stacks) is tracked in Linear (team
*Beethoven*, projects G2, G6, G7 and S) and lands as pull requests that pass the gate.

## Build and test

```sh
pixi install --locked          # reconstruct the exact environment from pixi.lock
pixi run --locked gate         # lint + typecheck + tests + the CrossHair/Z3 gate + vendor-check
pixi run --locked symbolic     # just the gate: python -m beethoven_qa gate
pixi run hooks                 # the pre-push gate is on from here
```

The gate runs before every push (`.githooks/pre-push`) and is the required check on `main`; pull
requests open ready for review and merge when it is green.

## Layout

```
pixi.toml / pixi.lock       the deterministic substrate (conda-forge + PyPI; linux-64)
src/beethoven_research/     the package: splits.py, the split-level check
vendor/beethoven-qa/        the pinned copy of beethoven-qa (ORIGIN names the release); never edited by hand
tests/                      pytest, run under typeguard
.githooks/ .github/ .claude/  the kit, the CI gate lane, the rule as code, session settings
.mcp.json                   Linear + GitHub MCP, configured from the environment (never hardcoded)
CLAUDE.md                   the working context and the non-negotiables
```

Every unit of build work is recorded in the project ledger
(`/mnt/project-files/ledger/LEDGER.md`) so the whole build is reproducible for the research report.
