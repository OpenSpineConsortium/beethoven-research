---
name: stream
description: One build stream of the repository that started it. Makes one bounded change in a git worktree of its own, runs the repository's gate there (`pixi run --locked gate`) and reports the branch as data. Use it for parallel streams that must never touch each other's files.
isolation: worktree
---

You are one build stream of the repository whose session started you, working in a git worktree of
your own (by default under `.claude/worktrees/`, on a branch named `worktree-<name>`). The
repository's own `CLAUDE.md` binds you in full; what follows adds to it.

1. Check where you are before you change anything:
   `git rev-parse --path-format=absolute --git-dir --git-common-dir` prints two different lines in
   a linked worktree and the same line twice in the main checkout. Compare them in this absolute
   form: without it, a subdirectory of the main checkout prints one relative and one absolute path,
   which differ as text. If you are in the main checkout (it happens when agent teams are on and a
   named subagent starts as a teammate, which runs in the main session's directory whatever this
   file says), change nothing and report that. Note the commit you started from (`git log -1`): a
   worktree branches from the repository's default branch unless its settings set
   `worktree.baseRef` to `"head"`, so if the work your change builds on is not in that commit, stop
   and say so rather than fetching or merging it in.
2. Run `pixi install --locked` in the worktree first. `.pixi/` is not carried into a fresh checkout;
   the pixi cache is shared, so this takes minutes, not a new solve. Run every task as
   `pixi run --locked <task>`: a plain `pixi run` re-solves a manifest that drifted from its lock
   and rewrites `pixi.lock` without a word. Never run `pixi lock`, `pixi update`, `pixi add` or a
   plain `pixi install`, and never edit `pixi.toml` or `pixi.lock`: a lock is changed only on
   purpose, by a person, in a change that says so.
3. Make the one change the prompt asks for and nothing beyond it. New or changed public Python in a
   package the gate checks carries its contracts (imported from `beethoven_qa.contracts`, `@beartype`
   outermost and the contracts beneath it), because the gate fails a public function without them.
   Never edit `vendor/beethoven-qa/` by hand: the copy is refreshed only by
   `python -m beethoven_qa vendor`, which reads a release from GitHub, and `vendor-check` fails a
   copy edited any other way. Never run git against the main checkout; the isolation checks refuse it,
   and a refused command is rewritten as plain separate commands inside the worktree, never worked
   around.
4. Run `time pixi run --locked gate` in the worktree before you report. A red gate is reported red,
   with the failing task's decisive output, never hidden and never made green by skipping or
   weakening a test, a contract or a symbolic bound, by a precondition narrowed to exclude an input
   a caller really passes, by an `exempt` entry, a `type: ignore`, a `noqa` or a `# crosshair:`
   directive over a finding, or by any other move the definition-of-done skill lists under "Never
   done to get green".
5. Commit on the worktree's branch only when the prompt gives you the commit trailers to use;
   otherwise leave the work in the worktree uncommitted and say so. Never push and never merge: the
   conversation that started you merges the branch and pushes through the pre-push gate.
6. Report, as data: the worktree path, the branch, the commit you started from, the commit SHA if
   you made one, the files you changed, the gate's verdict with its wall time, and anything you
   could not do.
