---
name: code-standards
description: Use when writing or changing Python code or GitHub workflows in a repository that runs beethoven-qa, to write code that clears the code standards (readability, design, duplication, coverage, security, supply chain) the first time, and to read and fix a standards failure.
---

# Writing to the code standards

`python -m beethoven_qa standards <group>|all` (the `standards` lane of `pixi run --locked gate`,
and one CI job per group with `standards (every group green)` the required check) blocks a change
whose **new or changed code** breaks a limit below. Old findings the change does not make worse are
reported and pass. The full account, with every limit's source, is `docs/STANDARDS.md` in
beethoven-qa (in a consumer: `vendor/beethoven-qa/` holds the package; the document is in the
release).

## The limits

| Group | Limit |
| - | - |
| readability | cognitive complexity at most 15; cyclomatic complexity at most 10; at most 4 parameters (`self` and `cls` not counted); at most 25 lines of code in a function body (docstring, comments and blank lines not counted) |
| design | per class: LCOM4 at most 1 (instance methods all joined through shared attributes of `self` or calls), DIT at most 5, CBO at most 14, WMC at most 50, RFC at most 50, NOC at most 10 |
| duplication | at most 3% of the new lines inside a block of 10 lines and 100 tokens that occurs elsewhere in the package |
| coverage | the tests run at least 80% of the new lines that can run |
| security | no new bandit finding of medium or high severity and confidence (`shell=True`, `eval`, unsafe YAML or pickle loading, weak hashes for security, binding to all interfaces, and the rest of bandit's list) |
| supply chain | every action pinned to a full 40-character commit (`uses: owner/repo@<sha> # vX.Y.Z`); no download piped into a shell (`curl ... \| sh`); top-level `permissions:` that grant no write (a job that writes says so on the job); no `${{ github.event.* }}` an outsider controls inside a `run:` script (pass it through `env:`); no checkout of a pull request's head under `pull_request_target` or `workflow_run` |

## Writing so the first run is green

- **One job per function.** Past about 20 lines of code, or a third level of nesting, split: the
  loop body, each branch of a long `if`/`elif`, the parsing of one input form, each become a small
  private function with a name that says what it decides. Return early instead of nesting; replace
  an `if`/`elif` ladder over values with a dict lookup.
- **Cognitive complexity charges nesting.** Each `if`, loop, `except` or ternary costs 1 plus its
  depth, and each run of mixed `and`/`or` costs 1. A helper that takes the inner loop resets the
  depth to zero.
- **More than four parameters** means the values travel together: a `NamedTuple` (or a pydantic
  model at a boundary) carries them, or the function does two jobs.
- **A class whose methods fall into two groups** that share nothing (LCOM4 2 or more) is two
  classes, or a class and free functions. Static and class methods and dunders are not counted.
- **Never copy a block.** The second time a run of ten similar lines is needed, it is a function.
  The detector compares tokens, so renaming the variables of a copy is not a way around it; and a
  copy that merely looks different is still a copy to the next reader.
- **Test the lines you add.** Every new branch needs a test that runs it; the coverage group reads
  only new lines, so an old untested module does not count against a change, and a new untested
  one does. Code run only through a subprocess does not count as run: test the function in-process.
- **No shell.** `subprocess.run([...])` with a list, never `shell=True`; `yaml.safe_load`; no
  `eval`, `exec` or `pickle` on anything read. A low-severity note (importing `subprocess`) is
  recorded and does not block.
- **Workflows.** Look the commit up (`git ls-remote --tags https://github.com/<owner>/<repo>`) and
  pin it with the tag as a comment; put `permissions: contents: read` (or `{}`) at the top; pass any
  `github.event` text into a script through `env:` and quote it as `"$VAR"`.

## When a group fails

1. Run that group alone, against the same base, to iterate quickly:
   `pixi run --locked python -m beethoven_qa standards <group> --base <base>` (`auto` finds where
   the branch left the default branch). Readability, design, duplication, security and supply
   chain take seconds; coverage runs the test suite.
2. Read the blocking lines: `path:line: rule: <what> is N, more than L (new)` or `(worse than
   before)`. `--sarif DIR` writes the whole record, old findings included.
3. Change the code. For readability and design, split the function or class as above; for
   duplication, extract the shared block; for coverage, write the missing tests; for security and
   supply chain, remove the weakness.
4. Run the group again, then the whole gate before pushing.

## What is never done

- Never raise a limit, add a configuration file, or add a `# noqa`, `# nosec`, `# pragma: no
  cover` or another tool's ignore comment to get green: none of them is honoured, and a limit
  changes only in a reviewed release of beethoven-qa.
- Never rename a function, move code into a file the package does not include, or exclude tests
  to make a finding look old or disappear. A renamed function's findings are new; that is by design.
- Never delete or weaken a test so coverage counts fewer lines, and never skip a test.
- Never split a function into pieces that only make sense together just to pass a count: the
  limits stand for readable code, and a reviewer reads the result.
