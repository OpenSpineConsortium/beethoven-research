"""The worked example: one module that clears the definition of done end to end.

It exists so the verification gate is demonstrably real on day one. CrossHair + Z3 prove the
contracts below (`python -m beethoven_qa gate`); beartype checks the calls at runtime; the pydantic
model is the external boundary; tests/test_example.py exercises both under typeguard. Its subject
is the one quantity the engine must never get wrong but plausible: how much of the student's quota
window is left. The arithmetic is deliberately linear so the proof is total rather than "no
counterexample found within the timeout" (Z3 decides linear integer arithmetic; products of two
unknowns it may not).

What this is not: the quota scheduler. How Claude Code reports the remaining window is not yet
measured (Linear BEE-121); until it is, nothing here claims a number for it.
"""

from __future__ import annotations

from beartype import beartype
from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt, model_validator

from beethoven_qa.contracts import ensure, require

__all__ = ["Window", "clamp", "remaining"]


@beartype
@require(lambda lo, hi: lo <= hi, "the interval must not be empty")
@ensure(lambda result, lo, hi: lo <= result <= hi, "the result lies inside the interval")
@ensure(
    lambda result, x, lo, hi: not (lo <= x <= hi) or result == x,
    "a value already inside the interval is returned unchanged",
)
def clamp(x: int, lo: int, hi: int) -> int:
    """Confine ``x`` to ``[lo, hi]``."""
    return max(lo, min(x, hi))


@beartype
@require(lambda used: used >= 0, "usage is a count and cannot be negative")
@require(lambda cap: cap > 0, "a window with no capacity is a configuration error, not a zero")
@ensure(lambda result, cap: 0 <= result <= cap, "what is left never exceeds the window")
@ensure(
    lambda result, used, cap: result == cap - used or used >= cap,
    "below the cap the arithmetic is exact",
)
@ensure(lambda result, used, cap: used < cap or result == 0, "at or above the cap nothing is left")
def remaining(used: int, cap: int) -> int:
    """Tokens still available in a window of ``cap`` after ``used`` were spent, never below zero."""
    return max(0, cap - used)


class Window(BaseModel):
    """A quota window as reported from outside: the boundary where untrusted numbers are checked.

    Validation happens once, here, with pydantic; everything past this model works on plain ints
    under beartype and the contracts above (``remaining(window.used, window.cap)``). The model
    carries no logic of its own, on purpose: CrossHair 0.0.111 does not construct a pydantic model
    symbolically, so a contract on a method here would be reported "Not confirmed" and never
    exercised (measured 2026-10-04: a ``left()`` returning ``cap + 1`` passed the gate).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    used: NonNegativeInt
    cap: PositiveInt

    @model_validator(mode="after")
    def _used_within_reason(self) -> Window:
        # Over-use by more than the whole window again is not a saturated window, it is a wrong
        # reading; refuse it at the boundary rather than let ``remaining`` quietly report zero.
        if self.used > 2 * self.cap:
            raise ValueError(f"used={self.used} is more than twice cap={self.cap}: bad reading")
        return self
