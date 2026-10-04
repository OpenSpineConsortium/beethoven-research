"""beethoven-qa: how code for the Beethoven engine is written and proven before it may run.

The definition of done (CLAUDE.md), Design by Contract through one import point
(``beethoven_qa.contracts``), the layering rule for runtime types, the CrossHair/Z3 gate any
repository runs on its own package (``python -m beethoven_qa gate``), the pinned copy consuming
repositories carry (``python -m beethoven_qa vendor``), and the Claude Code plugin that gives a
session the skills, the agent and the workflows to write new code to that bar and bring existing
code up to it (``plugin/``).
"""

__all__ = ["__version__"]

# Integer versions (1.0.0), never fractional; the release tag of the repository says the same.
__version__ = "1.0.0"
