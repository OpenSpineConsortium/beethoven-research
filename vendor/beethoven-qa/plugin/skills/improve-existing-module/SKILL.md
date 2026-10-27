---
name: improve-existing-module
description: Use when bringing an existing module up to the beethoven-qa bar (contracts, layered types, the CrossHair/Z3 proof) without silently changing what it does for its callers.
---

# Bringing existing code up to the bar

Existing code has callers that depend on what it does today, including what it does wrong. The job
is to make that behaviour stated and proven, and to change it only in the open: every change of
behaviour is a finding a person sees, with a test that shows it. The bar is the definition-of-done
skill (`/beethoven-qa:definition-of-done`); load it first if it is not already in context.
`/beethoven-qa:improve-module` runs these steps on one module as a workflow (its `args` are in the
script's header); its skeptics argue step 7 from the code and from each condition's verdict, and the
copy-and-break check of step 7 is yours to run. It refuses an exempt module after mapping it, since
`gate --module` would check nothing; a gate line beside the counterexamples (a public coroutine,
contracts on a pydantic model, a `# crosshair:` directive) goes to a repair rather than being taken
for a person's decision; and its report names each function set aside under
`[tool.beethoven-qa.unproven]` as not proven, with its reason. Where the plugin is not installed,
run its script with the Workflow tool's `scriptPath` from the checkout (`plugin/workflows/` here,
`vendor/beethoven-qa/plugin/workflows/` in a consuming repository).

## Where the full bar applies, and where it does not

icontract, the layering rule and the CrossHair gate apply to Python that may take dependencies: the
engine's repositories (beethoven-qa, beethoven-agents, the research pipeline), each with its pixi
environment and its pinned copy of beethoven-qa. They do not apply to:

- **hpcworker's framework**: it runs on the login node of every site it supports, so it keeps to
  the standard library of the oldest Python among them (3.6 today, because Wayne State's login node
  is CentOS 7, as that site's module in beethoven-sites records). icontract cannot be imported
  there and no pixi environment is added to it;
- **the browser and desktop editions**: TypeScript.

Those take the repository half (the pre-push gate, required checks, auto-merge, rulesets as code)
and their own language's checks (hpcworker's `framework/tests/run.sh` under that oldest Python; the
editions' own typecheck and test scripts). Improving code there means characterisation tests in
their own suite and the same rule on findings; it never means adding icontract or a new dependency.

## 1. Read the module and every caller

Read the whole module, then find every caller (`grep -rn` for the module and each public name,
across the repository and, for beethoven-qa itself, the consuming repositories). Write down, per
public function: what it does now, what callers pass it (types, ranges, empty and missing values),
what they do with the result, and what it reads or writes in the world.

## 2. Pin today's behaviour first

Before changing anything, write characterisation tests that pin what the code does now, and run
them on the unchanged code; they must pass there. Include the edges: zero, empty, negative, equal
bounds, `None` where callers can pass it, non-ASCII text, the largest real input, and the error each
bad input raises today. A failure here means your reading is wrong, not the code.

If a pinned behaviour looks wrong, pin it anyway, note it as a candidate finding beside the test,
and go on. Nothing is fixed in this step.

## 3. Separate pure logic from I/O

Move each decision into a pure public function that takes what the I/O read as arguments; the I/O
becomes private and uncontracted, or moves to a wiring module listed in `exempt` (wiring and the
command line only, never logic). This is a refactor: the characterisation tests pass unchanged after
it. Callers keep the names they import: a public function that must still do I/O is defined in the
`exempt` wiring module and re-exported under its old name (the gate checks a function where it is
defined), and it carries no contracts, so CrossHair never checks it as a target. Where moving it
would break more than it mends (a hook script that must stand alone, a loader its callers import
from the module), it keeps its contracts, which the tests check at run time, and a person names it
with its reason under `[tool.beethoven-qa.unproven]` in `pyproject.toml` (a function, never a
class or a method: a class is proven whole); the gate then never checks it as a target and says so
on every run. Neither keeps CrossHair out of its body: CrossHair runs every function a contracted
target calls, so a contracted function that calls either one does the I/O too while it is being
proven and is itself kept private, exempt or listed; and a listed function's contracts are assumed
by every proven caller, so the tests are their only check and must exercise them.

## 4. Contracts that state the intended behaviour

Add contracts from the callers' expectations, the docstring and the design notes, not from the
body: `@beartype` outermost, the contracts from `beethoven_qa.contracts` beneath it, a pydantic model
where untrusted data enters. Each one may refuse an input the code used to accept: check it against
the callers you listed in step 1, and if a caller really passes such input, the new check is a change
of behaviour and goes to step 6 as a finding, never in quietly.

## 5. Run the gate

`pixi run --locked gate`, then the gate on the module alone for each condition's verdict:
`pixi run --locked python -m beethoven_qa gate --module <package>.<module> --report-all` (the
definition-of-done skill says how to read it). Both of its checks run on the module whatever the
rest of the package looks like, so a sibling module still without contracts does not hide a
counterexample here. For a counterexample, first call the function with it in a test and watch it
fail.

## 6. A counterexample is a real bug or a wrong contract: decide which

- **Callers cannot produce the input** (it is impossible by construction, say two entries with the
  same name from a mapping that cannot hold duplicates): the contract is missing a precondition.
  Add the precondition that states that guarantee, and confirm in step 1's list that every caller
  gives it. Never a precondition that merely excludes the counterexample.
- **The contract promised what the intent never included** (and the code rightly does not do):
  correct it to the intent the callers rely on, and show the person the old wording, the new one and
  why. That needs the person's agreement, not the gate's; a contract is never loosened because a
  proof failed.
- **Callers can produce the input and the result is wrong**: a real bug. Report it to the person as
  a finding: what input, what happens, what should happen, and which callers can reach it. Fix it
  only together with a test that fails before the fix and passes after it, and change the
  characterisation test that pinned the old behaviour in the same change, named in the report.
  Never fixed silently, never folded into a refactor.
- **Not sure which**: ask the person, with the input and both readings. Do not guess.

## 7. Check the contracts are not vacuous

- **Trivially true**: `len(x) >= 0`, `result == result`, a type beartype already checks.
- **Restating the implementation**: `result == max(0, cap - used)` over a body of
  `max(0, cap - used)` proves nothing about intent. State the properties instead: in range, exact
  below the cap, zero at or above it.
- **Too weak for the failure that matters**: copy the module to a scratch directory, make one
  plausible wrong change there (drop a clamp, shift a bound by one, flip a comparison) and run
  CrossHair on the copy at the declared bound (the command for a copy is in the definition-of-done
  skill). No counterexample means a postcondition is missing for that failure.
- **Preconditions that admit nothing**: an `Unable to meet precondition` line, which fails the
  gate on every run (`--report-all` is accepted and changes nothing); either the contract is
  vacuous or it is stated over values CrossHair cannot build, such as a string held to a few
  values, which an enum states instead.
- **Preconditions narrower than what callers pass**, compared against step 1's list.

## 8. Report

Render the contracts with the review-contracts skill, then report, as data: the module; the
characterisation tests added; the refactors (no behaviour change, tests unchanged); the findings
(input, what happens, what should, fixed or waiting for a decision); each condition's verdict at
the declared bound ("Confirmed over all paths", or "no counterexample within N s"); the gate's
verdict with its wall time; anything not done.

Never skip, disable or weaken a test, a contract or the symbolic bound to get green, nor make any
other move the definition-of-done skill lists under "Never done to get green", and never change
behaviour without a finding and a test that shows it.
