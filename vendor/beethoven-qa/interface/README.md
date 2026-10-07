# What beethoven-qa offers

`INTERFACE.json` here is this repository's manifest under the Beethoven interface standard
([INTERFACES.md](https://github.com/OpenSpineConsortium/beethoven/blob/main/INTERFACES.md)): what
another repository may take from beethoven-qa, at which version, and who takes it today. It travels
in the pinned copy (`vendor/beethoven-qa/interface/` in a consumer), where the consumer's own
`python -m beethoven_qa boundary` reads it. beethoven-qa imports nothing of ours, so its manifest
lists no imports.

| Export | Kind | What it is |
| --- | --- | --- |
| `python` | python | The modules a consumer may import and the names each exports, listed in `python.json`: `beethoven_qa.contracts` (`require`, `ensure`, `invariant`, `DBC`, `ViolationError`, the one import point for the contract decorators) and `beethoven_qa.__version__`. Everything else of the package is private, and a consumer that imports it fails rule 4 of `boundary`. |
| `cli` | cli | The commands of `python -m beethoven_qa` a consumer runs from its copy, listed in `cli.json` with their options and exit codes: `gate`, `standards`, `vendor`, `vendor-check`, `boundary`; and the `[tool.beethoven-qa]` table of `pyproject.toml` they read. |
| `kit` | files | The five files every consumer carries byte for byte: `.githooks/pre-push`, `.github/workflows/automerge.yml`, `.github/workflows/rulesets.yml`, `.claude/hooks/session-start.sh`, `.github/workflows/standards.yml`. `vendor --kit` puts them in place and `vendor-check` compares them. |
| `plugin` | files | The Claude Code plugin `beethoven-qa` (`plugin/`) and the marketplace file that lists it (`.claude-plugin/marketplace.json`). It is files and not a program: Claude Code reads it from the copy as a directory marketplace, and no consumer imports it or runs it from its own code. |
| `docs` | text | `docs/CONSUMING.md` (how a repository takes beethoven-qa) and `docs/STANDARDS.md` (the code standards), read at the release; the pinned copy does not carry them. |

`python` is kept small on purpose: what a consumer imports is what this repository promises not to
break without raising the export's version, and the consumers import the contract decorators and
nothing else. The gate, the standards and the copy are reached through `cli`, so their modules can change
inside a release without breaking anyone.

Every export is at version 1. A change a consumer must act on (a name, a command or an option
removed or renamed, an exit code whose meaning changes) raises that export's version in the same
change as the code; an addition keeps it (the standard's "How a provider changes an export").
`tests/test_interface.py` holds these files to the code: every listed module and name imports, the
commands and options are the ones `__main__.py` accepts, and the kit is `vendor.KIT`.
