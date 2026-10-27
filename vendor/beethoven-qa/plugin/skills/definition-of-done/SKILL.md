---
name: definition-of-done
description: Use before writing or changing a Python module in any repository that runs the beethoven-qa gate, to know the bar the module must clear and how to run the gate and read what it reports.
---

# The definition of done

No module enters a run until it clears every step below. The first four are checked on every push
(the pre-push hook's `pixi run --locked gate`, whose proof of step 4 covers the modules the change
reaches) and in CI (`pixi install --locked`, then the gate's lanes); the last two are how the work
is done.
Every repository runs the same gate: beethoven-qa from its own `src/`, a consuming repository from
its pinned copy at `vendor/beethoven-qa/` (never edited by hand; `vendor-check` fails one that was).

1. **Deterministic environment.** The module resolves inside `pixi.lock` (exact versions, build
   strings, hashes, `linux-64`) and runs in the hashed Apptainer image, `--containall --cleanenv
   --no-home`. Install with `pixi install --locked` and run every task as
   `pixi run --locked <task>`: a plain `pixi run` re-solves a manifest that drifted from its lock
   and rewrites `pixi.lock` without a word, where `--locked` refuses. A lock changes only on
   purpose, by a person running pixi, in a change that says so; never by hand, never by a stray
   `pixi lock`, `pixi update`, `pixi add` or plain `pixi install`.
2. **Design by Contract.** icontract `@require`, `@ensure` and class invariants on every public
   function. A subclass only weakens a precondition and strengthens a postcondition (icontract
   enforces this when the base class is `DBC`).
3. **Layered runtime types**, below.
4. **Symbolic proof.** CrossHair + Z3 find no input that satisfies the preconditions and violates a
   postcondition, within the declared per-condition bound.
5. **Schema-aligned parsing** for any output an LLM structures: coercive repair with validation
   feedback, never a hard crash on a formatting slip. Not yet built (Linear G1-4); until it is, such
   output enters through a pydantic model and a validation failure is caught there and reported.
6. **The SpecPylot loop.** Contracts and code, the gate, the counterexample fed back, again, until
   proven (`/beethoven-qa:verify-module`). People review the contracts, not the lines.

The repository half: `.githooks/pre-push` runs `pixi run --locked gate` (`pixi run --locked hooks`
turns it on; `SKIP_GATE=1` skips it only knowingly, and the commit says why); the same gate is the
required CI check; pull requests open ready for review, `.github/workflows/automerge.yml` arms
auto-merge, and the required checks alone decide. A long-running step declares its progress signal
(a reference implementation, a quantifiable objective or a test suite) and commits after each
meaningful unit.

## The layering rule

| Layer | Where | Why there |
| - | - | - |
| pydantic | the external boundary: files, the scheduler, LLM output, the network | O(N) coercion once, where untrusted data enters |
| beartype | every public function, the internal hot paths | amortized O(1); cheap enough to leave on |
| typeguard | tests only: `install_import_hook("<package>")` in `tests/conftest.py`, before the package is first imported | deeper and slower, affordable only there |
| icontract | on top of the types | the behaviour types cannot state; what CrossHair proves |

## Writing a contracted function

- Import the decorators from `beethoven_qa.contracts` (`DBC`, `ViolationError`, `ensure`,
  `invariant`, `require`), the one import point. The one exception is a file that must stand alone,
  such as the agents' guard, baked into the image before the package is bound: it imports icontract
  directly. Need another icontract feature? Add it to `contracts.py` in its own reviewed change.
- `@beartype` outermost, the contracts beneath it. Measured 2026-10-04: CrossHair still reads the
  contracts through beartype's wrapper, and a wrong-typed argument fails as a beartype violation
  instead of a `TypeError` from inside a contract lambda.
- A lambda's parameter names must match the function's; `result` is the return value. Every
  condition carries a plain sentence: it is what the reviewer reads.
- The gate's first check fails a public module-level function without contracts ("nothing to prove"
  is reported, never passed). It sees module-level functions only; a public method is held to the
  same rule by review.

## Keeping a function provable

- **Pure core, I/O at the edges.** CrossHair *executes* every contracted function with symbolic
  arguments. Anything that reads files, imports modules, runs subprocesses, touches the network or
  reads the clock or randomness stays private (leading underscore) and uncontracted, or lives in a
  module listed in `exempt`: wiring and the command line only, never logic. Pass in what the edge
  read: the caller reads the clock, the function takes `now`. A public function that must do the
  I/O itself (a loader the command line and the tests both call) keeps its contracts and is named,
  with its reason, under `[tool.beethoven-qa.unproven]`; the gate never checks it as a target,
  prints it on every run, and fails on an entry that names no contracted function, a class or a
  method (a class is proven whole, as one target, so I/O stays out of a contracted class). Logic
  CrossHair measurably cannot run goes there too, its reason naming the fault; logic it can run,
  never. Listing does nothing more: CrossHair runs the body of every function a contracted target
  calls, listed or not, so a contracted function that calls a listed one does the I/O too while it
  is being proven and is itself kept private, exempt or listed; and a listed function's contracts
  are assumed by every proven caller, never checked through it, so the tests are their only check
  and must exercise them.
- **Bounded loops.** A loop whose count is a symbolic integer multiplies the paths: measured, a
  `for i in range(n)` sum came back "Not confirmed". Prefer a closed form, a loop over the items of
  an argument, or a constant bound.
- **Linear arithmetic.** Z3 decides linear integer arithmetic; products of two unknowns it may not.
  `result == cap - used` can be proven over all paths; `2 * result == n * (n - 1)` may never be.
- **Integers over floats.** CrossHair does reason about IEEE floats (measured: it found the rounding
  counterexample to `(a + b) - b == a`), but a total proof comes from integer linear arithmetic. In
  proven logic carry counts, seconds or basis points as `int` and convert at the edge; a float
  postcondition states a tolerance, never exact equality.
- **Honest preconditions.** A precondition says what callers really guarantee. One that excludes an
  input callers pass, so a proof goes through, hides the bug it was written to find. One that states
  the function's real domain, excluding only what no caller ever passes (name the callers, and the
  check in them that guarantees it), is a specification, not a dodge.

## The `[tool.beethoven-qa]` table (`pyproject.toml`)

```toml
[tool.beethoven-qa]
package = "beethoven_qa"            # the top-level package the gate imports and checks
src = "src"                         # its directory, inside the repository
exempt = ["beethoven_qa", "beethoven_qa.contracts", "beethoven_qa.__main__"]  # wiring only
per_condition_timeout = 20          # seconds per condition, 1 to 600: the declared bound
```

## Running the gate and reading it

- `pixi run --locked gate`: lint (ruff), typecheck (mypy strict), test (pytest under typeguard),
  symbolic, the code standards, and in a consuming repository vendor-check.
  `/beethoven-qa:gate-until-green` fixes it round by round.
- `pixi run --locked standards` is `python -m beethoven_qa standards all`: readability, design,
  duplication, coverage, security and supply chain, each on its own, blocking only what the change
  made since where the branch left the default branch. One group:
  `python -m beethoven_qa standards <group> --base <ref>`. How to write to them and read a failure
  is the `code-standards` skill.
- `pixi run --locked symbolic` is `python -m beethoven_qa gate`: both checks over the whole package,
  the contract rule and CrossHair at the declared bound. Both always run, and it fails on either.
  With a base (`--base <ref>`, or `BEETHOVEN_QA_BASE`, which the pre-push hook sets to `auto`),
  CrossHair proves only the modules the change since that commit reaches, their importers among
  them, and every module where the change touches what every proof reads; `pyproject.toml` is read
  by its keys, so a comment, an unproven entry added or reworded, a boundary entry or the table of
  a tool in `scope.QUIET_TOOLS` (bandit, black, codespell, flake8, isort, mypy, pylint, pyright,
  pytest, ruff) proves nothing and an unproven entry removed proves the function it named, while
  any other key proves every module, as does a link or a submodule git tracks; the contract rule
  still covers the whole package.
- One module, every condition's verdict: `pixi run --locked python -m beethoven_qa gate --module
  <package>.<module> --report-all`, the dotted name of `src/<package>/<module>.py` (a subpackage's
  module is `<package>.<sub>.<module>`). Both checks on that module alone, at the declared
  `per_condition_timeout`; nobody passes a timeout by hand. It refuses, with exit 2 and having
  checked nothing, a module that is not part of the package, lies outside it or is exempt.
- A scratch copy outside the package (the check that the contracts can fail) is not a module the
  gate checks, so CrossHair runs on it directly, at the same declared bound and the way the gate
  starts it: `pixi run --locked python -m beethoven_qa._crosshair check <the copy>
  --analysis_kind=icontract --per_condition_timeout=<N> --report_all`, N the
  `per_condition_timeout` under `[tool.beethoven-qa]`, never another number. The runner is
  CrossHair's own command line with icontract's bookkeeping made first; `python -m crosshair`
  alone crashes with a RecursionError in code that calls a contracted function many times.

What the lines mean (CrossHair 0.0.111, measured 2026-10-04):

- `pacing.py:15: error: false when calling resume_at(0, 0, 1) (which returns 0)` (the worked
  example below, with its margin forgotten): the function, the input (arguments in the function's
  order) and the violated condition, which is the one at that line (the `@ensure`, or its lambda
  when it spans lines). Read its sentence there.
- `share.py:9: error: ZeroDivisionError:  when calling share(0, 0)`: the body raised for an input
  the preconditions allow; the line is in the body.
- On every run (`--report-all` is accepted and changes nothing): `info: Confirmed over all paths.`
  is a total proof of that condition; `info: Not confirmed.` means no counterexample within the
  bound, not a proof; `info: Unable to meet precondition.` means CrossHair found no input that
  meets the preconditions within the bound, either because none exists (they contradict each
  other) or because they are stated over values it cannot build (restate them over an enum), so
  nothing behind them was checked. The gate fails on that line, and counts the three verdicts
  under each target and, as its last line before the verdict, over the run.

The gate is green on "Not confirmed". Say "no counterexample within N s per condition", N the
declared `per_condition_timeout`, unless the line says "Confirmed over all paths". The bound is
seconds, not paths, so a busier or slower machine explores less and a counterexample can appear on
one machine and not on another; wherever it appears it is dealt with, never dismissed because the
gate was green elsewhere.

Before deciding anything about a counterexample, call the function with it in a test and watch it
fail. If the concrete call does not fail, the report is not reproduced: it is a fault of the tool,
not of the function. Record the input, the line and the CrossHair version for a person, and never
change the code, loosen a contract or lower the bound to make it go away. The one change that may
exclude such an input is a precondition that states the function's real domain, excluding only what
no caller ever passes, as the callers' own checks show. Seen 2026-10-04: CrossHair 0.0.111 reported
an `AssertionError` at `return sorted(old - new)` in `beethoven_qa.vendor.stale_files`, its own
internal assert tripped by symbolic strings holding NUL characters, which the same call, run
concretely, does not raise. The code gained exactly such a precondition, that every name is a safe
relative path (`is_safe_relpath`), and a refusal in its caller, which turns an unsafe name away
before it reaches `stale_files`; that domain also keeps the fault away. The bound and the
postconditions did not change. If no honest precondition excludes the input, the gate stays red and
a person decides.

## Never done to get green

Skipping, disabling or marking expected-to-fail a test; deleting or weakening a contract; narrowing
(strengthening) a precondition so that a counterexample's input is excluded while a real caller
passes that input; lowering `per_condition_timeout` or changing anything else under
`[tool.beethoven-qa]`, such as listing a module with logic in `exempt` or a function CrossHair can
run under `unproven`; a `type: ignore`, a `noqa`, a `# crosshair:` directive, or a ruff or mypy
configuration exclusion over a real finding; editing
`pixi.toml`, `pixi.lock` or `vendor/beethoven-qa/` to get green, or running `pixi lock`,
`pixi update`, `pixi add` or a plain `pixi install`; editing a CI workflow, a ruleset or a hook;
`SKIP_GATE=1` without the reason in the commit. A precondition that states the function's real
domain, excluding only what no caller ever passes, is not narrowing in this sense: name the callers
you checked. A red gate is reported red, with its decisive output.

## Worked example

```python
from beartype import beartype

from beethoven_qa.contracts import ensure, require


@beartype
@require(lambda margin: margin >= 0, "the safety margin is a duration, never negative")
@ensure(lambda result, now: result >= now, "never a time in the past")
@ensure(lambda result, reset_at, margin: result >= reset_at + margin, "never before the reset")
@ensure(
    lambda result, now, reset_at, margin: result == now or result == reset_at + margin,
    "no wait longer than the reset needs",
)
def resume_at(now: int, reset_at: int, margin: int) -> int:
    """The earliest second, at or after ``now``, at which a step held for a spent window may run."""
    return max(now, reset_at + margin)
```

Pure (the caller reads the clock), integer and linear: every postcondition came back "Confirmed
over all paths". With the margin forgotten (`max(now, reset_at)`), CrossHair reported
`resume_at(0, 0, 1) (which returns 0)` against "never before the reset". Its tests pin the
concrete cases (`resume_at(100, 50, 10) == 100`, `resume_at(0, 50, 10) == 60`) and a
`ViolationError` for `margin=-1`.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.
