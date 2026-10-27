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
| beethoven-qa | `cli` | 3 | `vendor/beethoven-qa/` | `python -m beethoven_qa gate`, `vendor-check`, `standards`, `boundary` and `report`, the pixi tasks of the same names, and `coach`, the session hook's |
| beethoven-qa | `kit` | 3 | `vendor/beethoven-qa/` | the five kit files, byte for byte (`.githooks/pre-push`, `.github/workflows/automerge.yml`, `rulesets.yml`, `standards.yml`, `.claude/hooks/beethoven-qa-session-start.sh`) |
| beethoven-qa | `plugin` | 1 | `vendor/beethoven-qa/` | the Claude Code plugin's skills and workflows, read by a session that writes code here |

The four imports share one copy and one record, `vendor/beethoven-qa/ORIGIN`, in beethoven-qa's
own form (repository, ref, commit, tree digest, file count), written by beethoven-qa's `vendor`
command, which from 3.0.0 reads the release from GitHub through the person's own `gh` sign-in at
a tag on `main`, and checked by `pixi run --locked vendor-check`; it is the one pull that runs the
provider's code (the standard's rule 3). The copy pinned today is beethoven-qa 10.0.0 (`cli` at
version 3 since 5.0.0, which added `report`; `kit` at version 3 since 6.0.0, which renamed the
session-start hook), which carries beethoven-qa's own
`interface/INTERFACE.json`: its `boundary` command, the gate's `boundary` lane, checks these four
entries against that manifest and every `beethoven_qa` import against its `python` export, and
`tests/test_interface.py` holds this repository to the same rules from its own side.

Nothing here takes anything from beethoven-agents yet: a study will run inside its image and its
orchestration, and when it does, that is an import declared here first, against an export
beethoven-agents lists. Nothing here reads, runs or copies a file of another repository's checkout.
