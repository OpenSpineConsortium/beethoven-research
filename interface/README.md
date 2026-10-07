# What beethoven-research offers other repositories

This repository is one sealed unit of the Beethoven ecosystem (the interface standard:
https://github.com/OpenSpineConsortium/beethoven/blob/main/INTERFACES.md). It is the last link of
the direction beethoven-qa <- beethoven-agents <- beethoven-research, so it offers nothing: no
other repository may take anything from it, and `INTERFACE.json` lists no export. Everything here
is private and may change in any pull request.

## What this repository takes

| From | Export | Version | Into | Record |
| --- | --- | --- | --- | --- |
| beethoven-qa | `python` | 1 | `vendor/beethoven-qa/` | `beethoven_qa.contracts` only: `splits.py` takes `require` and `ensure`, the tests `ViolationError` |
| beethoven-qa | `cli` | 1 | `vendor/beethoven-qa/` | `python -m beethoven_qa gate`, `vendor-check` and `standards`, the pixi tasks of the same names |
| beethoven-qa | `kit` | 1 | `vendor/beethoven-qa/` | the five kit files, byte for byte (`.githooks/pre-push`, `.github/workflows/automerge.yml`, `rulesets.yml`, `standards.yml`, `.claude/hooks/session-start.sh`) |
| beethoven-qa | `plugin` | 1 | `vendor/beethoven-qa/` | the Claude Code plugin's skills and workflows, read by a session that writes code here |

The four imports share one copy and one record, `vendor/beethoven-qa/ORIGIN`, in beethoven-qa's
own form (repository, ref, commit, tree digest, file count), written by beethoven-qa's `vendor`
command from a checkout at a release tag and checked by `pixi run --locked vendor-check`; that is
the one copy the standard lets be fetched another way (its rule 3). The copy pinned today is
beethoven-qa 2.0.0, which carries beethoven-qa's own `interface/INTERFACE.json`: its `boundary`
command, the gate's `boundary` lane, checks these four entries against that manifest and every
`beethoven_qa` import against its `python` export, and `tests/test_interface.py` holds this
repository to the same rules from its own side.

Nothing here takes anything from beethoven-agents yet: a study will run inside its image and its
orchestration, and when it does, that is an import declared here first, against an export
beethoven-agents lists. Nothing here reads, runs or copies a file of another repository's checkout.
