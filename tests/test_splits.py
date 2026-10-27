"""Runtime checks of the split-level check.

These are not the proof; CrossHair is (`python -m beethoven_qa gate`). They pin the concrete
behaviour a reader expects, show each layer refusing what it should, and would catch a contract
that was weakened by mistake.
"""

from typing import Any

import pytest
from beartype.roar import BeartypeCallHintParamViolation
from beethoven_qa.contracts import ViolationError
from pydantic import ValidationError

from beethoven_research.splits import Split, SplitLevel, disjoint, shared_groups


def test_disjoint_partitions_share_nothing() -> None:
    assert shared_groups(("p1", "p2", "p3"), ("p4", "p5")) == ()
    assert disjoint(("p1", "p2", "p3"), ("p4", "p5"))


def test_a_leak_names_each_group_once_in_evaluation_order() -> None:
    training = ("p1", "p2", "p2", "p3")
    # Read backwards this names p1 first, so the order is pinned, not only the set.
    evaluation = ("p9", "p3", "p1", "p3", "p8", "p1")
    assert shared_groups(training, evaluation) == ("p3", "p1")
    assert shared_groups(training, ("p1", "p3")) == ("p1", "p3")
    assert not disjoint(training, evaluation)


def test_empty_partitions_are_disjoint() -> None:
    assert shared_groups((), ()) == ()
    assert shared_groups(("p1",), ()) == ()
    assert shared_groups((), ("p1",)) == ()
    assert disjoint((), ("p1",))


def test_leave_one_cadaver_out() -> None:
    # DeepFluoro: six subjects, so the held-out cadaver appears nowhere in training.
    cadavers = tuple(f"c{i}" for i in range(1, 7))
    for held_out in cadavers:
        training = tuple(c for c in cadavers if c != held_out)
        assert disjoint(training, (held_out,))
        assert shared_groups(cadavers, (held_out,)) == (held_out,)


def test_a_case_without_a_group_is_refused() -> None:
    with pytest.raises(ViolationError):
        shared_groups(("p1", ""), ("p2",))
    with pytest.raises(ViolationError):
        disjoint(("p1",), ("",))


def test_a_padded_or_blank_group_is_refused() -> None:
    # "P001 " is a second spelling of "P001": accepted, it would hide the leak.
    with pytest.raises(ViolationError):
        shared_groups(("P001",), ("P001 ",))
    with pytest.raises(ViolationError):
        disjoint((" ",), ("\t",))


def test_beartype_refuses_wrong_types() -> None:
    # A list where the signature says tuple, typed Any so a type checker lets the wrong call
    # through to beartype, which is what this test is for.
    a_list: Any = ["p1"]
    with pytest.raises(BeartypeCallHintParamViolation):
        shared_groups(a_list, ("p2",))


def test_split_boundary_validation() -> None:
    # As a study record would carry it: JSON lists, the level by its name.
    record = {
        "corpus": "VinDr-SpineXR",
        "level": "study",
        "training": ["s1", "s2"],
        "evaluation": ["s3"],
    }
    split = Split.model_validate(record)
    assert split.level is SplitLevel.STUDY
    assert split.training == ("s1", "s2")
    assert disjoint(split.training, split.evaluation)
    with pytest.raises(ValidationError):
        Split.model_validate({"corpus": "x", "level": "scan", "training": [], "evaluation": []})
    with pytest.raises(ValidationError):
        Split.model_validate(
            {"corpus": "x", "level": "patient", "training": ["p1", ""], "evaluation": []}
        )
    with pytest.raises(ValidationError):
        Split.model_validate({"corpus": "", "level": "patient", "training": [], "evaluation": []})
    for padded in (" ", "\t", "P001 ", " P001"):
        with pytest.raises(ValidationError):
            Split.model_validate(
                {"corpus": "x", "level": "patient", "training": ["P001"], "evaluation": [padded]}
            )
    with pytest.raises(ValidationError):
        Split.model_validate(
            {"corpus": "x", "level": "patient", "training": [], "evaluation": [], "extra": 1}
        )


def test_a_refused_record_is_reported_without_its_labels() -> None:
    # The record's labels never reach a log through the boundary's own error.
    with pytest.raises(ValidationError) as refused:
        Split.model_validate(
            {"corpus": "x", "level": "patient", "training": ["c-7731", ""], "evaluation": []}
        )
    assert "c-7731" not in str(refused.value)
