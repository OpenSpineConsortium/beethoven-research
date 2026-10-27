"""CrossHair's command line, started the way the gate needs it (``gate.crosshair_command``).

icontract keeps the functions whose contracts it is checking in a set that its first contracted
call makes and stores in a ContextVar (``icontract._checkers._IN_PROGRESS``), and every contracted
call after adds to it and takes from it. Under CrossHair that first call is made inside an
explored path, where CrossHair's own ``set()`` stands in for the builtin, and the set made there
outlives the path: each addition and removal wraps it in one more layer, so the membership test
icontract makes on entry walks one frame deeper with every contracted call the process makes,
until Python's recursion limit raises RecursionError inside icontract and CrossHair reports it as
an error of the function under check. Measured 2026-10-09 with CrossHair 0.0.111 and icontract
2.7.3: ``gate.uncontracted``, which calls the contracted ``is_public`` once per entry, failed so at
40 s per condition (the more paths, the more calls), and a contracted function that calls another
500 times fails on its first path. Made here, before CrossHair traces anything, the set is the
builtin and stays so, and nothing about what CrossHair checks changes.
"""

import sys

import icontract._checkers


def _bookkeeping_before_crosshair() -> None:
    # icontract makes the set only when the ContextVar holds none, so one made here is the one
    # every contracted call of this process uses.
    if icontract._checkers._IN_PROGRESS.get() is None:
        icontract._checkers._IN_PROGRESS.set(set())


def _start(argv: list[str]) -> None:
    _bookkeeping_before_crosshair()
    # Imported here and never at the top: importing CrossHair turns icontract's own checks off in
    # the whole process (its core_and_libs replaces the three assertion functions), so a process
    # that only imports this module, the gate's first check or a test, keeps its contracts.
    from crosshair.main import main

    main(argv)


if __name__ == "__main__":
    _start(sys.argv[1:])
