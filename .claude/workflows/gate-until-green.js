export const meta = {
  name: 'gate-until-green',
  description: 'Run pixi run gate and fix what fails, one round at a time, until it is green or two rounds make no progress',
  phases: [
    { title: 'Gate', detail: 'pixi run gate: ruff, mypy, pytest under typeguard, CrossHair/Z3' },
    { title: 'Fix', detail: 'one fixer per round, in this checkout' },
  ],
}
// Saved by the skeleton thread on 2026-10-04 in the shape the workflows page calls "keep fixing until
// a check passes". One fixer at a time, in this checkout: parallel fixers would edit the same files.
// Never skips, disables or weakens a test, a contract or a symbolic bound (CLAUDE.md).
//
// args: { maxRounds?: number }   rounds of gate-then-fix; the default is 4; 0 runs no agent and is the
//                                syntax check of this file.

const GATE = {
  type: 'object',
  required: ['passed', 'failures'],
  properties: {
    passed: { type: 'boolean', description: 'true only if `pixi run gate` exited 0' },
    failures: {
      type: 'array',
      items: {
        type: 'object',
        required: ['lane', 'summary'],
        properties: {
          lane: { type: 'string', description: 'ruff, mypy, pytest or symbolic' },
          summary: { type: 'string', description: 'one paragraph: what failed and where' },
          excerpt: { type: 'string', description: 'the decisive lines of output, verbatim' },
        },
      },
    },
  },
}

const FIX = {
  type: 'object',
  required: ['changed', 'files', 'note'],
  properties: {
    changed: { type: 'boolean' },
    files: { type: 'array', items: { type: 'string' } },
    note: { type: 'string', description: 'what was wrong and what the fix is; or why nothing could be changed' },
  },
}

const maxRounds = args && typeof args.maxRounds === 'number' ? args.maxRounds : 4
const rounds = []
let lastKey = null
let stuck = 0

for (let round = 1; round <= maxRounds; round++) {
  const gate = await agent(
    'Run `pixi run gate` in this checkout (lint, typecheck, tests under typeguard, CrossHair/Z3). ' +
      'Report passed=true only if the command exited 0. For each failing lane give its name (ruff, mypy, ' +
      'pytest, symbolic), a one-paragraph summary, and the decisive lines of its output verbatim. Change no file.',
    { label: `gate round ${round}`, phase: 'Gate', schema: GATE },
  )
  if (!gate) {
    log(`round ${round}: the gate agent returned nothing; stopping`)
    break
  }
  rounds.push({ round, passed: gate.passed, failures: gate.failures.length })
  if (gate.passed) {
    log(`round ${round}: the gate is green`)
    return { green: true, rounds }
  }
  const key = JSON.stringify(gate.failures.map((f) => [f.lane, f.summary]))
  stuck = key === lastKey ? stuck + 1 : 0
  lastKey = key
  if (stuck >= 2) {
    log(`round ${round}: the same failures two rounds running; stopping`)
    return { green: false, rounds, failures: gate.failures }
  }
  const fix = await agent(
    'The gate failed. Fix the cause in this checkout, minimally, lane by lane:\n' +
      gate.failures.map((f) => `- ${f.lane}: ${f.summary}\n${f.excerpt || ''}`).join('\n') +
      '\nNever skip, disable or weaken a test, a contract or the symbolic bound to get green; fix the code ' +
      'or the contract that is wrong. Do not commit. Report which files you changed and why.',
    { label: `fix round ${round}`, phase: 'Fix', schema: FIX },
  )
  if (!fix) {
    log(`round ${round}: the fix agent returned nothing; stopping`)
    break
  }
  log(`round ${round}: ${fix.changed ? `${fix.files.length} file(s) changed` : 'nothing changed'}: ${fix.note}`)
  if (!fix.changed) stuck += 1
}

return { green: false, rounds, failures: lastKey ? JSON.parse(lastKey) : [] }
