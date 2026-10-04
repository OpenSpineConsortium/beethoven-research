"""``bee``: Claude accounts and the state hand-off between them (greg's state-management guide).

One self-contained piece, so it can move to the agentic framework's repository whole: the account
list and switching (``accounts``), the hand-off file and the hooks that keep the project state
(``handoff``), the ``.claudeignore`` hook (``claudeignore``), the command line (``cli``, run as
``python -m beethoven_research.bee``), the hook runner for a checkout (``hook.sh``), the grid
wrapper (``bee-grid.sh``) and the shell helpers (``shell/``). Its documents are ``docs/bee/`` and
its tests ``tests/bee/``; what it changes outside these three folders is listed in
``docs/bee/STATE_HANDOFF.md``.

``handoff.py`` and ``claudeignore.py`` import nothing of the package: the image runs them by path
as hooks, as it runs the guard.
"""
