"""The split level of a corpus, and the check that its partitions are disjoint at that level.

REFORMS, for the first study (plan/SPINE_FIRST_STUDY.md E.2 and G2 item 1; BUILD_PLAN's G2 row):
the level at which a corpus's partitions must not overlap (patient, study, cadaver or CT source
volume) is written in the study record before the first training job, and a test fails if any
evaluation case shares a patient, cadaver or source volume with a training case. This module is
that check. A partition is the group, at the declared level, of each of its cases; the check
returns the groups both partitions contain, and an empty answer means disjoint.

The groups come from the study record (``Split``, the pydantic boundary), written by a person or
by the ingestion step that read the corpus's own metadata on the grid; nothing here reads a corpus
or a DICOM header. A group is a study-local pseudonym the record assigns, never a patient's
identifier (a medical record number, a name, an accession number, a DICOM PatientID), because
every failure of this module names the groups it was given, by design: a contract violation
prints its arguments, beartype its argument, a failing test its assertion, and each of them lands
in a job log or a test report. What may identify a patient, and where it may be kept, is greg's
PHI statement (Linear BEE-165). The one echo that is not by design, pydantic repeating the record
in a validation error, is switched off on ``Split``.

A group is named exactly: not empty, and with no surrounding whitespace, since ``"P001"`` and
``"P001 "`` would otherwise be two groups and a leaking pair would pass as disjoint. Any other
spelling difference (case, leading zeros) is the record's to settle before the check runs.

Why the answer is the leaking groups and not a count: a leak is fixed by naming the group, and a
count is the kind of plausible wrong number this project fears (one shared cadaver among six is a
different finding from one shared patient among five thousand).
"""

from __future__ import annotations

from enum import StrEnum

from beartype import beartype
from beethoven_qa.contracts import ensure, require
from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["Split", "SplitLevel", "disjoint", "shared_groups"]


class SplitLevel(StrEnum):
    """The unit two partitions must not share, declared per corpus before training (E.2)."""

    PATIENT = "patient"  # VinDr-SpineXR (or study), BUU-LSPINE, CSXA
    STUDY = "study"  # VinDr-SpineXR's fixed split is by study
    CADAVER = "cadaver"  # DeepFluoro: six subjects, so leave-one-cadaver-out or none
    SOURCE_VOLUME = "source_volume"  # every render set: the CT volume its frames came from


@beartype
@require(
    lambda training: all(g != "" and g == g.strip() for g in training),
    "every training case names its group, with no surrounding whitespace",
)
@require(
    lambda evaluation: all(g != "" and g == g.strip() for g in evaluation),
    "every evaluation case names its group, with no surrounding whitespace",
)
@ensure(
    lambda result, training: all(g in training for g in result),
    "every group returned is in the training partition",
)
@ensure(
    lambda result, evaluation: all(g in evaluation for g in result),
    "every group returned is in the evaluation partition",
)
@ensure(
    lambda result, training, evaluation: all(g in result for g in evaluation if g in training),
    "no shared group is missed",
)
@ensure(lambda result: len(set(result)) == len(result), "each shared group is named once")
@ensure(
    lambda result, evaluation: all(
        evaluation.index(a) < evaluation.index(b) for a, b in zip(result, result[1:], strict=False)
    ),
    "the groups come in the order the evaluation partition first names them",
)
def shared_groups(training: tuple[str, ...], evaluation: tuple[str, ...]) -> tuple[str, ...]:
    """The groups both partitions contain, in the order the evaluation partition first names them.

    ``training`` and ``evaluation`` hold one entry per case: that case's group at the declared
    level. Empty means the partitions are disjoint at that level.
    """
    found: list[str] = []
    for group in evaluation:
        if group in training and group not in found:
            found.append(group)
    return tuple(found)


@beartype
@require(
    lambda training: all(g != "" and g == g.strip() for g in training),
    "every training case names its group, with no surrounding whitespace",
)
@require(
    lambda evaluation: all(g != "" and g == g.strip() for g in evaluation),
    "every evaluation case names its group, with no surrounding whitespace",
)
@ensure(
    lambda result, training, evaluation: result == all(g not in training for g in evaluation),
    "disjoint means no evaluation case shares its group with a training case",
)
def disjoint(training: tuple[str, ...], evaluation: tuple[str, ...]) -> bool:
    """Whether no evaluation case shares its group, at the declared level, with a training case."""
    return len(shared_groups(training, evaluation)) == 0


class Split(BaseModel):
    """One corpus's two partitions as the study record declares them: the boundary.

    Validation happens once, here, with pydantic; the decision is ``shared_groups`` and
    ``disjoint`` on the plain tuples. The model carries no logic of its own, on purpose: CrossHair
    0.0.111 does not construct a pydantic model, and beethoven-qa's gate refuses a contract on one.
    """

    # hide_input_in_errors: a refused record is reported by its fault, never by its contents.
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    corpus: str
    level: SplitLevel
    training: tuple[str, ...]
    evaluation: tuple[str, ...]

    @field_validator("corpus", "training", "evaluation")
    @classmethod
    def _named(cls, value: str | tuple[str, ...]) -> str | tuple[str, ...]:
        # A case with no group at the declared level cannot be placed in either partition, and a
        # padded label is a second spelling of one group; refuse the record rather than let the
        # pair pass as "disjoint" by accident. The message names the fault, never a label.
        items = (value,) if isinstance(value, str) else value
        if any(item == "" or item != item.strip() for item in items):
            raise ValueError(
                "the corpus and every case's group must be named, with no surrounding whitespace"
            )
        return value
