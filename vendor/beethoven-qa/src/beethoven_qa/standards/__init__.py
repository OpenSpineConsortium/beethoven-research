"""The code standards: each group of the Drive file "Auditable Code Quality Standards" (greg,
2026-10-04) as its own check, run alone, passing or failing alone, and blocking a pull request
alone (``.github/workflows/standards.yml``, one job per group).

- ``readability``: cognitive complexity (Campbell), cyclomatic complexity (McCabe), parameters and
  function length (Code Climate's limits).
- ``design``: the Chidamber and Kemerer suite (WMC, DIT, NOC, CBO, RFC) and LCOM4.
- ``duplication``: copied code in the new lines (Sonar way: at most 3%).
- ``coverage``: tests reaching the new lines (Sonar way: at least 80%).
- ``security``: weaknesses named by CWE (bandit), the catalogue ISO/IEC 5055 builds on.
- ``supply-chain``: the workflow files, by OpenSSF Scorecard's checks of them.

Every group gates the new code only (``findings`` and ``newcode`` say how), writes a SARIF 2.1.0
log (``sarif``), and takes its limits from this package, never from the repository it checks
(``group``). ``python -m beethoven_qa standards <group>|all`` runs them (``run``);
``docs/STANDARDS.md`` is the whole account.
"""
