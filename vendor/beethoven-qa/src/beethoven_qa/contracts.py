"""Design-by-Contract conventions for the engine (Goal 1, step 2 of the definition of done).

Why a module of its own: every public function in the engine carries contracts, CrossHair reads
those contracts to prove them, and humans review the contracts rather than the lines. Keeping the
decorators behind one import point means a module's contracts always come from the same checked
library and the gate (beethoven_qa.gate) can recognise them by one rule.

The layering rule, decided in BUILD_PLAN §3 and applied everywhere:

- ``pydantic`` models at the external boundary (files, the scheduler, LLM output, the network):
  O(N) coercion once, where untrusted data enters.
- ``beartype`` on internal hot paths: amortized O(1) checks on every call, cheap enough to leave on
  in production.
- ``typeguard`` only in the test enclave (tests/conftest.py installs its import hook), where its
  deeper, slower checking is affordable.
- ``icontract`` preconditions and postconditions on top, for the *behavioural* promise the types
  cannot express; these are what CrossHair + Z3 prove.

Liskov: a subclass may only weaken a precondition and strengthen a postcondition. icontract enforces
this itself when ``DBC`` is the base class and ``@require``/``@ensure`` are used on overrides.

Decorator order: ``@beartype`` outermost, the contracts beneath it. Measured on 2026-10-04 with a
deliberately false postcondition: CrossHair still reads the icontract contracts through beartype's
wrapper and reports the counterexample at a clean file:line, the gate still finds
``__preconditions__`` on the outer object, and a wrong-typed argument fails as a beartype violation
instead of a raw ``TypeError`` from inside a contract lambda (which is what the reverse order gives,
because the lambda runs before any type was checked).
"""

from icontract import DBC, ViolationError, ensure, invariant, require

__all__ = ["DBC", "ViolationError", "ensure", "invariant", "require"]
