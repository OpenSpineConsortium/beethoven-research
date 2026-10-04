"""Runtime checks of the worked example.

These are not the proof; CrossHair is (tools/verify_gate.py). They pin the concrete behaviour a
reader expects, show each layer refusing what it should, and would catch a contract that was
weakened by mistake.
"""

import pytest
from beartype.roar import BeartypeCallHintParamViolation
from pydantic import ValidationError

from beethoven_research.contracts import ViolationError
from beethoven_research.example import Window, clamp, remaining


def test_clamp_concrete() -> None:
    assert clamp(5, 0, 10) == 5
    assert clamp(-3, 0, 10) == 0
    assert clamp(42, 0, 10) == 10


def test_clamp_refuses_empty_interval() -> None:
    with pytest.raises(ViolationError):
        clamp(1, 10, 0)


def test_remaining_saturates_at_zero() -> None:
    assert remaining(0, 100) == 100
    assert remaining(40, 100) == 60
    assert remaining(100, 100) == 0
    assert remaining(150, 100) == 0


def test_remaining_refuses_negative_usage_and_empty_window() -> None:
    with pytest.raises(ViolationError):
        remaining(-1, 100)
    with pytest.raises(ViolationError):
        remaining(0, 0)


def test_beartype_refuses_wrong_types() -> None:
    with pytest.raises(BeartypeCallHintParamViolation):
        remaining("40", 100)  # type: ignore[arg-type]


def test_window_boundary_validation() -> None:
    assert Window(used=40, cap=100).left() == 60
    with pytest.raises(ValidationError):
        Window(used=-1, cap=100)
    with pytest.raises(ValidationError):
        Window(used=0, cap=0)
    with pytest.raises(ValidationError):
        Window(used=201, cap=100)  # a bad reading, not a saturated window
    with pytest.raises(ValidationError):
        Window(used=1, cap=100, extra=1)  # type: ignore[call-arg]
