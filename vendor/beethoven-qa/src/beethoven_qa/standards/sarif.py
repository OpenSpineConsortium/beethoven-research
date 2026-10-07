"""The report every standards group writes: a SARIF 2.1.0 log (OASIS Static Analysis Results
Interchange Format), so an auditor reads one format whatever tool made the finding.

One run per tool: its ``tool.driver`` names the tool and the version that ran (from the lock), its
rules say what each checks and which standard it implements, and each result carries the finding's
place, its ``partialFingerprints`` (rule, file and anchor, never the line; ``findings.fingerprint``)
and its ``baselineState`` against the base commit, the fields SARIF defines for telling a new result
from an old one. ``runs[].automationDetails.id`` names the group, so the logs of the six groups can
be read side by side. Every log the tests write validates against the OASIS schema
(``tests/data/sarif-schema-2.1.0.json``).

Nothing here uploads anything: GitHub's code scanning would take these files, but on a private
repository it needs GitHub Code Security, which is not bought (greg, 2026-10-04). CI keeps the logs
as a workflow artifact instead.
"""

# Annotations are evaluated here (no ``from __future__ import annotations``): typeguard, in the
# test enclave, reads a NamedTuple's field types from the module of the function it checks, where a
# string such as "Changes" names nothing.

from typing import NamedTuple

from beartype import beartype

from beethoven_qa.contracts import ensure, require
from beethoven_qa.standards.findings import LEVELS, STATES, Finding, fingerprint

__all__ = ["FINGERPRINT", "SCHEMA", "Rule", "Run", "sarif_log"]

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
# The key of the one partial fingerprint each result carries; the "/v1" changes if its recipe does.
FINGERPRINT = "beethovenQa/v1"


class Rule(NamedTuple):
    """One rule as SARIF lists it under its tool."""

    id: str  # e.g. "readability/cognitive-complexity"
    name: str  # an identifier, e.g. "CognitiveComplexity"
    text: str  # one sentence: what the rule checks, with its limit
    standard: str  # the standard it implements, and where its limit comes from
    help_uri: str
    tags: tuple[str, ...] = ()


class Run(NamedTuple):
    """What one tool found for one group, with the state of each finding against the base."""

    group: str
    tool: str
    version: str
    uri: str
    rules: tuple[Rule, ...]
    findings: tuple[Finding, ...]
    states: tuple[str, ...]


def _sound(run: Run) -> bool:
    ids = [r.id for r in run.rules]
    return (
        len(set(ids)) == len(ids)
        and len(run.findings) == len(run.states)
        and all(s in STATES for s in run.states)
        and all(
            f.rule in ids and f.level in LEVELS and 1 <= f.line <= f.end_line for f in run.findings
        )
    )


def _result(finding: Finding, state: str, index: int) -> dict[str, object]:
    return {
        "ruleId": finding.rule,
        "ruleIndex": index,
        "level": finding.level,
        "message": {"text": finding.message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": finding.path, "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": finding.line, "endLine": finding.end_line},
                }
            }
        ],
        "partialFingerprints": {
            FINGERPRINT: fingerprint(finding.rule, finding.path, finding.anchor)
        },
        "baselineState": state,
        "properties": {
            "anchor": finding.anchor,
            "value": finding.value,
            "limit": finding.limit,
            "tags": sorted(set(finding.tags)),
        },
    }


def _rule(rule: Rule) -> dict[str, object]:
    return {
        "id": rule.id,
        "name": rule.name,
        "shortDescription": {"text": rule.text},
        "fullDescription": {"text": rule.standard},
        "helpUri": rule.help_uri,
        "defaultConfiguration": {"level": "error"},
        "properties": {"tags": sorted(set(rule.tags))},
    }


def _results(log: dict[str, object]) -> list[list[dict[str, object]]]:
    runs = log["runs"]
    return [run["results"] for run in runs] if isinstance(runs, list) else []


@beartype
@require(lambda runs: all(_sound(run) for run in runs), "each finding names a rule of its run")
@ensure(lambda result: result["version"] == "2.1.0", "SARIF 2.1.0")
@ensure(lambda result, runs: len(result["runs"]) == len(runs), "one SARIF run per tool run")
@ensure(
    lambda result, runs: [len(r) for r in _results(result)] == [len(run.findings) for run in runs],
    "every finding is reported, and nothing else",
)
@ensure(
    lambda result, runs: all(
        res["ruleId"] == run.rules[res["ruleIndex"]].id and res["baselineState"] == state
        for res_list, run in zip(_results(result), runs, strict=True)
        for res, state in zip(res_list, run.states, strict=True)
    ),
    "each result points at its own rule and carries its own state",
)
def sarif_log(runs: tuple[Run, ...]) -> dict[str, object]:
    """The SARIF 2.1.0 log of ``runs``, ready for ``json.dumps``."""
    return {"$schema": SCHEMA, "version": "2.1.0", "runs": [_run(run) for run in runs]}


def _run(run: Run) -> dict[str, object]:
    ids = [r.id for r in run.rules]
    return {
        "tool": {
            "driver": {
                "name": run.tool,
                "version": run.version,
                "informationUri": run.uri,
                "rules": [_rule(r) for r in run.rules],
            }
        },
        "automationDetails": {"id": f"beethoven-qa/standards/{run.group}/"},
        "originalUriBaseIds": {
            "%SRCROOT%": {"description": {"text": "The root of the repository."}}
        },
        "columnKind": "unicodeCodePoints",
        "results": [
            _result(f, s, ids.index(f.rule)) for f, s in zip(run.findings, run.states, strict=True)
        ],
    }
