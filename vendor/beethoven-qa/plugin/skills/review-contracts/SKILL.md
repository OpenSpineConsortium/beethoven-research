---
name: review-contracts
description: Use when a module's contracts are to be shown to a person for review, or checked for being vacuous, restating the code, too weak or narrower than what callers pass, before the module is called done.
---

# Presenting contracts for review

People review the contracts, not the lines. A review is only as good as its rendering: each
condition in words a reviewer can hold against what the function is for, the evidence of how far it
was proven, and every reason to doubt it named. Reviewing changes no file; a better contract is
written as a proposal for the person to accept.

## Gather

1. **The public surface.** Every module-level function whose name does not start with an underscore
   (checked against the module's `__all__`), and the public methods of its public classes, with
   their class invariants. The gate's contract rule sees module-level functions only, so a public method
   without contracts is found here or nowhere.
2. **The declared bound**: `per_condition_timeout` under `[tool.beethoven-qa]` in `pyproject.toml`.
3. **Each condition's verdict**: the gate on the module alone, at the declared bound:
   `pixi run --locked python -m beethoven_qa gate --module <package>.<module> --report-all`. Each
   line names a condition by its line number: `Confirmed over all paths`, `Not confirmed` (no
   counterexample within the bound), `Unable to meet precondition`, or an `error:` line with a
   counterexample.
4. **The callers**: `grep -rn` for each public name; note what each caller really passes.
5. **The failure that matters**, per function: the answer that would be wrong but plausible. Copy
   the module to a scratch directory, make that wrong change there, and run CrossHair on the copy at
   the declared bound (the command for a copy is in the definition-of-done skill). A counterexample
   means the contracts catch it; none means they do not.

## Render

One block per function, one line per condition: the plain words first, written from the expression
(not copied from its description string), then the expression, so the reviewer can check one
against the other.

```
resume_at(now, reset_at, margin) -> int        (the definition-of-done skill's worked example)
  requires  the margin is not negative                       margin >= 0
  ensures   never earlier than now                           result >= now
  ensures   never earlier than the reset plus the margin     result >= reset_at + margin
  ensures   it is one of those two, so no longer wait        result == now or result == reset_at + margin
  bound     20 s per condition; all three confirmed over all paths
  failure   margin forgotten, max(now, reset_at): caught, resume_at(0, 0, 1) returns 0
  flags     none
```

- `requires` for each precondition, `ensures` for each postcondition, `invariant` once per class.
- `bound`: the declared seconds, and per condition "confirmed over all paths" or "no counterexample
  within N s". Never "proven" for a condition that was not confirmed over all paths.
- `failure`: the wrong-but-plausible change tried in step 5 and whether CrossHair caught it.
- `flags`: each flag below that applies, with its evidence, or `none`.

Close with one summary line: functions, conditions, confirmed over all paths, not confirmed, flags.

## Flag

- **Vacuous.** True whatever the function does (`len(x) >= 0`, `result == result`); a restatement of
  a type beartype already checks; or preconditions that admit no input CrossHair could find, shown
  by `Unable to meet precondition` (the gate's `--report-all` fails it; without that option the gate
  passes it in silence).
- **Restates the implementation.** The postcondition is the body again (`result == max(0, cap -
  used)` over `return max(0, cap - used)`), so it proves the code equals itself and says nothing
  about intent. Propose the properties instead: the range, the exact case, the saturating case. A
  function that is itself a definition (a predicate such as `is_public`) can only be restated: say
  so, and ask the reviewer to check the definition against the intent.
- **Missing the failure that matters.** Step 5's wrong change went uncaught, or no postcondition
  mentions the result at all. Name the failure and propose the postcondition that would catch it.
- **Dodges real inputs.** A precondition excludes something a caller passes (cite the caller and
  the input), or was narrowed until a counterexample went away. Either the caller is wrong, a
  finding, or the precondition is. A precondition that states the function's real domain, excluding
  only what no caller ever passes, is not this flag: say which callers were checked and what in them
  guarantees it.
- **Not confirmed.** Say why if it can be seen: a loop to a symbolic count, a product of two
  unknowns, floats. Propose the linear or bounded form when there is one; never a shorter timeout.
- **Words and expression disagree.** The description string says one thing and the lambda checks
  another; the reviewer must be told which one the code follows. Found this way on 2026-10-04 in
  `remaining` of `beethoven_qa.example`: its sentence said the answer saturates at zero at or above
  the cap, its expressions allowed any value from zero to the cap there, and a copy returning the
  full window when it was spent passed every condition, confirmed over all paths. The fix was a
  third postcondition, `used < cap or result == 0`, which that copy now fails.
- **Uncovered.** A public method without contracts, or logic in a module listed in `exempt`; or a
  function listed under `[tool.beethoven-qa.unproven]` whose contracts no test exercises: the gate
  never checks it as a target, and every proven caller assumes its contracts rather than checking
  them, so the tests are their only check. Name the test that calls it, or the flag stands.

## Never

Edit the module or its contracts while reviewing; mark a flag resolved without the person; call a
condition proven on the strength of a green gate alone; leave out a flag because the gate is green.
