export const meta = {
  name: 'gate-until-green',
  description: 'Run the repository gate (pixi run --locked gate) and fix what fails, one round at a time, until it is green or stops making progress; every fix is gated again before the result is reported',
  whenToUse: 'A repository whose pixi gate task (pixi run --locked gate) is red and the cause is in its own code, tests or contracts',
  phases: [
    { title: 'Gate', detail: 'pixi run --locked gate: every task the repository chains into it' },
    { title: 'Fix', detail: 'one fixer per round, in this checkout, always followed by another gate' },
  ],
}
// The shape the workflows page calls "keep fixing until a check passes", for any repository that
// defines a pixi `gate` task (beethoven-qa's own: lint, typecheck, test, symbolic; a consuming
// repository adds vendor-check). Written in beethoven-research on 2026-10-04 and moved into the
// beethoven-qa plugin at the split. One fixer at a time, in this checkout: parallel fixers would edit
// the same files. Never skips, disables or weakens a test, a contract or a symbolic bound, and never
// re-solves the lock: every gate runs `pixi run --locked`, which refuses a drifted manifest instead
// of rewriting pixi.lock behind the run.
//
// Every fix is followed by a gate, so what the result reports is what the code does after the last
// edit, never failures from before it. It stops when the gate is green; when the fixer changes
// nothing (gating unchanged code again could only repeat the round); when the same failures, keyed
// on what the tools print (test node ids, file:line of a finding), survive two fixes; when a fix
// needs a person; or when the gate runs are used up.
//
// args: { maxRounds?: number }   gate runs; the default is 4, so at most 3 fixes, each gated again
//                                (the last round gates and does not fix); 0 runs no agent and is the
//                                syntax check of this file.

const GATE = {
  type: 'object',
  required: ['passed', 'failures'],
  properties: {
    passed: { type: 'boolean', description: 'true only if `pixi run --locked gate` exited 0' },
    failures: {
      type: 'array',
      items: {
        type: 'object',
        required: ['lane', 'ids', 'summary'],
        properties: {
          lane: { type: 'string', description: 'the failing pixi task, such as lock, lint, typecheck, test, symbolic or vendor-check' },
          ids: {
            type: 'array',
            items: { type: 'string' },
            description: 'what failed, copied verbatim from the tool, one per entry: a pytest node id (tests/test_x.py::test_y), the file:line and code of a ruff or mypy finding, the CrossHair target and file:line of a counterexample, the path vendor-check names; never prose, never a duration or a count',
          },
          summary: { type: 'string', description: 'one paragraph: what failed and where' },
          excerpt: { type: 'string', description: 'the decisive lines of output, verbatim' },
        },
      },
    },
  },
}

const FIX = {
  type: 'object',
  required: ['changed', 'files', 'note', 'needs_person'],
  properties: {
    changed: { type: 'boolean' },
    files: { type: 'array', items: { type: 'string' }, description: 'each file changed, as a path relative to the root of this checkout' },
    note: { type: 'string', description: 'what was wrong and what the fix is; or why nothing could be changed' },
    needs_person: { type: 'string', description: 'empty, or the failure whose only fix is one of the moves this prompt forbids, and that move' },
  },
}

// The moves that make a gate green without making the code right. Spelled out here because the
// plugin serves repositories whose own instructions may not repeat them; the definition-of-done
// skill lists the same ones under "Never done to get green".
const NEVER =
  'Never done to get green, whatever the failure: skipping, disabling, deselecting or marking expected-to-fail a ' +
  'test; deleting or weakening a contract; narrowing (strengthening) a precondition so that a counterexample\'s ' +
  'input is excluded while a real caller passes that input; lowering per_condition_timeout or changing anything ' +
  'else under [tool.beethoven-qa] in pyproject.toml, adding a module to its exempt list included; a ' +
  '`# type: ignore`, a `noqa`, a `# crosshair:` directive, or a ruff or mypy configuration change (an ignore, an ' +
  'exclude, a per-file rule) over a real finding; editing pixi.toml or pixi.lock, or running `pixi lock`, `pixi update`, `pixi add` or a ' +
  'plain `pixi install` (only `pixi install --locked` and `pixi run --locked <task>` are run); editing ' +
  'vendor/beethoven-qa/ by hand; editing a CI workflow, a ruleset or a hook (.github/, .githooks/, .claude/). ' +
  "A precondition that states the function's real domain is not narrowing when no caller passes the input it " +
  'excludes: name the callers you checked. When the only fix is one of these moves, do not make it: leave that ' +
  "failure, say which move it would take in needs_person, and fix the rest; it is a person's decision."

// Files a fix may not change on its own, whatever its note says: the configuration the gate reads
// (pytest's own files among them, which it reads before pyproject.toml, and any conftest.py, which
// can deselect or reconfigure tests from a hook), the lock, the pinned copy, and the CI, ruleset
// and hook files. Checked on the fixer's own list, so a fix that touched one is gated once more and
// then stopped for a person.
function personsFiles(files) {
  return files.filter((f) => {
    const p = String(f)
      .replace(/\\/g, '/')
      .replace(/^.*\/\.claude\/worktrees\/[^/]+\//, '')
      .replace(/^\.\//, '')
    return (
      /(^|\/)(pyproject\.toml|pixi\.toml|pixi\.lock|ruff\.toml|\.ruff\.toml|mypy\.ini|\.mypy\.ini|pytest\.ini|\.pytest\.ini|setup\.cfg|tox\.ini|conftest\.py)$/.test(p) ||
      /(^|\/)(vendor\/beethoven-qa|\.github|\.githooks)\//.test(p) ||
      /(^|\/)\.claude\/(?!worktrees\/)/.test(p)
    )
  })
}

// Agents list failures in no promised order and describe them in fresh words each round; the key
// is built only from what the tools printed, sorted, so "the same failures" is a property of the
// set and survives a reworded summary.
function failureKey(failures) {
  return JSON.stringify(
    failures.map((f) => JSON.stringify([f.lane, (f.ids || []).map((s) => String(s).trim()).sort()])).sort(),
  )
}

let maxRounds = 4
if (args && args.maxRounds !== undefined) {
  if (Number.isInteger(args.maxRounds) && args.maxRounds >= 0) maxRounds = args.maxRounds
  else log(`maxRounds ${JSON.stringify(args.maxRounds)} is not a whole number of rounds; using ${maxRounds}`)
}
const rounds = []
let lastKey = null
let lastFailures = []
let stuck = 0
// Set when a fix must be gated once more and then handed to a person, whatever that gate says.
let stopAfterGate = null

if (maxRounds === 0) {
  log('maxRounds is 0: no agent runs; this is the syntax check of the script')
  return { green: false, rounds, failures: [], stopped: 'maxRounds is 0' }
}

for (let round = 1; round <= maxRounds; round++) {
  phase('Gate')
  const gate = await agent(
    'Run `pixi run --locked gate` in this checkout: every task the repository chains into its gate, usually ' +
      'lint (ruff), typecheck (mypy), test (pytest under typeguard), symbolic (CrossHair/Z3) and, in a ' +
      'repository that carries a copy of beethoven-qa, vendor-check. Report passed=true only if the command ' +
      'exited 0. If pixi stops before any task because the lock file is not up to date with the workspace, ' +
      'that is the failure to report (lane "lock"); never re-solve it. For each failing task give its name, ' +
      'the identifiers of what failed copied from its output (pytest node ids, file:line and code of each ' +
      'lint or type finding, the target and file:line of each CrossHair counterexample), a one-paragraph ' +
      'summary, and the decisive lines of its output verbatim. Change no file, and never run `pixi lock`, ' +
      '`pixi update`, `pixi add` or a plain `pixi install`.',
    { label: `gate round ${round}`, phase: 'Gate', schema: GATE },
  )
  if (!gate) {
    const stopped = `the gate agent returned nothing in round ${round}` + (round > 1 ? '; the last fix has not been gated' : '')
    log(`${stopped}; stopping`)
    return { green: false, rounds, failures: null, stopped }
  }
  rounds.push({ round, passed: gate.passed, failures: gate.failures.length })
  lastFailures = gate.failures
  if (stopAfterGate) {
    log(`round ${round}: the gate is ${gate.passed ? 'green' : 'red'} after the last fix; stopping: ${stopAfterGate}`)
    return { green: false, gate_passed: gate.passed, rounds, failures: gate.failures, stopped: stopAfterGate }
  }
  if (gate.passed) {
    log(`round ${round}: the gate is green`)
    return { green: true, rounds }
  }
  if (gate.failures.length === 0) {
    log(`round ${round}: the gate is red but no failing task was named; stopping for a person`)
    return { green: false, rounds, failures: [], stopped: 'the gate is red and named no failing task' }
  }
  const key = failureKey(gate.failures)
  stuck = key === lastKey ? stuck + 1 : 0
  lastKey = key
  if (stuck >= 2) {
    const stopped = 'the same failures survived two fixes'
    log(`round ${round}: ${stopped}; stopping`)
    return { green: false, rounds, failures: gate.failures, stopped }
  }
  // A fix nothing gates afterwards would be reported as red on stale failures.
  if (round === maxRounds) break
  phase('Fix')
  const fix = await agent(
    'The gate failed. Fix the cause in this checkout, minimally, task by task:\n' +
      gate.failures.map((f) => `- ${f.lane}: ${f.summary}\n${(f.ids || []).join('\n')}\n${f.excerpt || ''}`).join('\n') +
      `\n\n${NEVER}\n\n` +
      'Fix the code, the test or the contract that is wrong. If vendor-check fails, the copy is refreshed only ' +
      'by `python -m beethoven_qa vendor` from a beethoven-qa checkout: leave it and say so. Do not commit. ' +
      'Report which files you changed, as paths relative to the root of this checkout, and why.',
    { label: `fix round ${round}`, phase: 'Fix', schema: FIX },
  )
  if (!fix) {
    // Whatever it edited before it failed is unknown, so it is gated like any fix.
    stopAfterGate = `the fix agent returned nothing in round ${round}; whatever it changed is left in the checkout for a person`
    log(`round ${round}: ${stopAfterGate}`)
    continue
  }
  rounds[rounds.length - 1].fix = { changed: fix.changed, files: fix.files, note: fix.note, needs_person: fix.needs_person || '' }
  log(`round ${round}: ${fix.changed ? `${fix.files.length} file(s) changed` : 'nothing changed'}: ${fix.note}`)
  const touched = personsFiles(fix.files)
  if (!fix.changed) {
    // Nothing changed, so the failures of this round's gate are still the current ones.
    const stopped = `the fixer changed nothing in round ${round}` + (fix.needs_person ? `; a person must decide: ${fix.needs_person}` : '')
    log(`round ${round}: ${stopped}; stopping`)
    return { green: false, rounds, failures: gate.failures, stopped }
  }
  if (touched.length) {
    stopAfterGate = `the fixer changed ${touched.join(', ')}, which only a person may change to get green; review that change before keeping it`
  } else if (fix.needs_person) {
    stopAfterGate = `a person must decide: ${fix.needs_person}`
  }
}

// Only the last round's gate breaks out of the loop, so these failures are those of the code as the
// last fix left it.
const stopped = `not green within ${maxRounds} gate run(s)`
log(stopped)
return { green: false, rounds, failures: lastFailures, stopped }
