---
name: write-verified-module
description: Use when writing a new Python module or a new public function for a repository that runs the beethoven-qa gate, to work in the order that ends with a proven module whose contracts a person has reviewed.
---

# Writing a new module to the bar

The contracts are the specification and the part a person reviews, so they come first and the code
is written to satisfy them, never the other way round. The bar itself, and how to read the gate, is
the definition-of-done skill (`/beethoven-qa:definition-of-done`); load it first if it is not
already in context.

## Before the first line

- **One bounded change per stream**: one module, or one function and its tests. Anything larger is
  split into streams before work starts.
- **Parallel streams never share a checkout.** Each one is the plugin's stream agent
  (`beethoven-qa:stream`, `isolation: worktree`): its own worktree and branch, `pixi install
  --locked` first, `pixi run --locked gate` before it reports, no push and no merge. The
  conversation that started the streams merges their branches and pushes through the pre-push gate.
- **Find the edges.** Read the neighbouring modules and the code that will call this one. Write down
  what enters from outside (a file, the scheduler, the network, LLM output: a pydantic model), what
  is decided inside (plain types under beartype, contracted, pure) and what touches the world
  (private, uncontracted, or an `exempt` wiring module). Logic never goes to the edge.
- **A long-running piece of work names its progress signal** (a reference implementation, a
  quantifiable objective or a test suite) before it starts, and commits after each meaningful unit
  when the prompt gives commit trailers.

## 1. The specification, as contracts

Write each public function's signature, types, docstring and contracts, with a body of
`raise NotImplementedError`. For each function:

- **Preconditions**: what every caller really guarantees, and nothing more. A precondition that
  refuses inputs callers pass is a hole, not a specification.
- **Postconditions**: what the function promises, starting with the failure that matters, the answer
  that would be wrong but plausible: a count off by one, a time in the past, a path that leaves its
  directory, a total that silently saturates. If no postcondition would catch that answer, the
  specification is not done.
- **Invariants** for a class whose state must hold between calls (base class `DBC`, so an override
  may only weaken a precondition and strengthen a postcondition).
- **Plain sentences** on every condition, and arithmetic kept linear over integers, so the proof can
  be total.

Render the contracts in plain words (the review-contracts skill) and show them to the person before
implementing. When nobody is in the conversation (a `-p` step), go on, and put that rendering in the
report so the review happens there.

## 2. Pin the concrete behaviour with tests

In `tests/test_<module>.py`, before the implementation:

- the cases a reader expects, with literal inputs and outputs;
- the edges: zero, empty, equal bounds, the largest input a real caller passes;
- each layer refusing what it should: `ViolationError` (from `beethoven_qa.contracts`) for a broken
  precondition, `BeartypeCallHintParamViolation` for a wrong type, pydantic's `ValidationError` at
  the boundary.

They run under typeguard's import hook (`tests/conftest.py`) and fail until step 3. They are not the
proof; they pin what a reader expects and catch a contract weakened by mistake.

## 3. Implement

The smallest code that satisfies the contracts. `@beartype` outermost, the contracts beneath it,
imported from `beethoven_qa.contracts`. No file, clock, subprocess, import or network inside a
contracted function, nor through anything it calls: CrossHair runs it with symbolic arguments, and
runs the body of every function it calls, a function listed under `[tool.beethoven-qa.unproven]`
included (listing only keeps a function from being a target of its own; its contracts are then
assumed by every proven caller and checked by the tests alone). Prefer a loop over the items of an
argument or to a constant; a loop to a symbolic count rarely comes back confirmed. Write to the
code standards' limits from the start (the `code-standards` skill): short functions of at most four
parameters and little nesting, no copied block, a test for every new branch.

## 4. The gate

`pixi run --locked gate`, the code standards among its lanes. A red lane is fixed at its cause: the code, or a contract that was wrong
in the specification, and a changed contract goes back to the person in step 6.
`/beethoven-qa:gate-until-green` runs the gate and one fixer per round, gating every fix again,
until green or stuck.

## 5. Prove the module

Run `/beethoven-qa:verify-module` with `{ module: "src/<package>/<module>.py" }`. It runs the gate
on that module alone (`gate --module <package>.<module> --report-all`), hands each counterexample
(function, input, violated condition, or a precondition CrossHair could not meet) to one repair, and
repeats until the module has no counterexample within the declared bound (what the workflow calls
"proven"), the same counterexamples come back, a repair changes nothing, or its rounds (three by
default) run out; then it returns them for a person. A module the gate refuses (not part of the
package, or exempt) comes back as an error, and one in which it checked nothing as not proven. A
repair changes the code or the wrong contract, never a bound, never a precondition narrowed to
exclude an input a caller really passes (one that states the real domain is allowed, and every
precondition a repair added is listed in the result with the callers it checked), never the
`exempt` list.

Where the plugin is not installed (a cloud project thread reading an attached checkout), run the same
script with the Workflow tool's `scriptPath`: `plugin/workflows/verify-module.js` in a beethoven-qa
checkout, `vendor/beethoven-qa/plugin/workflows/verify-module.js` in a consuming one, same `args`.

The result carries each condition's verdict (`verdicts`, from `--report-all`); outside the
workflow the same comes from `pixi run --locked python -m beethoven_qa gate --module
<package>.<module> --report-all`. Keep them: "Confirmed over all paths", or "Not confirmed" (no
counterexample within the bound). An `Unable to meet precondition` line fails the gate: CrossHair
found no input that meets the preconditions; fix the specification before anything else.

Last, one check that the contracts can fail: copy the module to a scratch directory (its absolute
imports still resolve from `PYTHONPATH`), make one plausible wrong change there (drop the clamp,
shift a bound by one, flip a comparison), and run CrossHair on the copy at the declared bound (the
command for a copy is in the definition-of-done skill). It must report a counterexample. If it does
not, a postcondition is missing for exactly that failure; add it and return to step 4.

## 6. Present the contracts

Render the final contracts with the review-contracts skill and report, as data: the module, the
gate's verdict and wall time, each condition's verdict at the declared bound, the contracts in plain
words, every contract that changed since step 1 and why, and anything not done. "Proven" is said
only of a condition confirmed over all paths; anything else is "no counterexample within N s per
condition".

## Never

Skip, disable or weaken a test, a contract or the symbolic bound to get green, or make any other
move the definition-of-done skill lists under "Never done to get green"; edit `pixi.toml`,
`pixi.lock` or `vendor/beethoven-qa/` by hand, or run `pixi lock`, `pixi update`, `pixi add` or a
plain `pixi install`; put logic in an `exempt` module; push or merge from a stream.
