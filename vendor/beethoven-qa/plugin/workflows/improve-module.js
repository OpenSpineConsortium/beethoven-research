export const meta = {
  name: 'improve-module',
  description: 'Bring one existing module up to the definition of done without changing what it does: map it, pin its behaviour with tests, add contracts and layered types, prove them, let two skeptics attack the contracts, and report them in plain words',
  whenToUse: 'An existing Python module of a package the repository gate checks has no contracts or weak ones; pass args {module: "src/<package>/<name>.py"}',
  phases: [
    { title: 'Read', detail: 'map the module: public functions, callers, tests, which parts do I/O' },
    { title: 'Pin', detail: 'characterisation tests of what the code does now, passing on the unchanged code' },
    { title: 'Contract', detail: 'contracts and the beartype/pydantic layering, pure logic apart from I/O, no behaviour change' },
    { title: 'Prove', detail: 'the gate on this module (gate --module, --report-all) and the pinned tests, a repair between proofs, until both pass or it is stuck' },
    { title: 'Vacuity', detail: 'two independent skeptics try to show a contract says nothing that matters' },
    { title: 'Report', detail: 'the contracts in plain words and the findings, for the human reviewer' },
  ],
}
// For any repository whose gate is the beethoven-qa gate. The order is the point: behaviour is
// pinned by tests before anything changes, so "no behaviour change" is checked, not claimed;
// contracts come next; the gate then proves them; and because a proven contract can still promise
// nothing, two skeptics read them before a person does (humans review the contracts, not raw
// lines). One editor at a time in this checkout (Read, Pin, Contract and the repairs run one after
// another); the skeptics and the reporter only read. Nothing is committed: the progress signal is
// the pinned tests and the gate's verdict on the module, rerun in every proving round, and the last
// log line says what to commit.
//
// The verdict is the gate's own on this one module (`gate --module ... --report-all`): both of its
// checks run on the module whatever the rest of the package looks like, so a sibling module still
// without contracts neither hides a counterexample here nor sends the person elsewhere; every
// condition's verdict comes back; and a condition whose preconditions CrossHair could not meet fails
// the gate instead of passing in silence. Every pixi command is `--locked`, so no step re-solves the
// lock. What the Pin phase must leave alone is checked against the module's sha256 recorded at the
// start of the run, not against git, which knows nothing of an untracked module and counts the
// person's own uncommitted edits as the run's; and the pinned tests, once they pass, are held to
// their own sha256 in every proving round, so a change to a pinned assertion that no repair named
// is caught instead of proven against. No digest is ever handed to the agent it checks: an agent
// told the value it must match could echo it instead of running sha256sum.
//
// A module listed in exempt stops the run after the Read phase: `gate --module` refuses an exempt
// module, so nothing could be proven, and nothing is changed. A gate that refuses the module in the
// Prove phase ends the run there, without the skeptics or the reporter, who would read as proven
// contracts the gate never checked.
//
// Two kinds of change are a person's to choose, so the workflow records them as decisions and makes
// neither. A public module-level function that does I/O cannot carry contracts (CrossHair would run
// its I/O with symbolic arguments) and cannot stay without them (the gate's first check fails it);
// clearing it means renaming it, which changes its callers, or moving it to an exempt wiring module,
// which changes pyproject.toml, and both reach beyond one module. And a precondition or type check
// that would reject an input the code accepts today is a change of behaviour, however wrong the
// input looks.
//
// args: { module: 'src/<package>/<name>.py', maxRounds?: number }
//       module is required: without it nothing runs and the result is { error }.
//       maxRounds, the proving rounds, defaults to 3 (a repair runs only between two proofs, so the
//       last round proves and does not repair); 0 runs no agent at all and is the syntax check of
//       this file.

const LIST = { type: 'array', items: { type: 'string' } }

const MAP = {
  type: 'object',
  required: ['found', 'package', 'dotted', 'exempt', 'module_sha256', 'module_dirty', 'functions', 'test_files', 'notes'],
  properties: {
    found: { type: 'boolean', description: 'the file exists and is a module of the package [tool.beethoven-qa] names' },
    package: { type: 'string', description: 'the package [tool.beethoven-qa] names in pyproject.toml' },
    dotted: { type: 'string', description: 'the dotted name of the module, such as pkg.sub.name' },
    exempt: { type: 'boolean', description: 'the module is listed in exempt under [tool.beethoven-qa]' },
    module_sha256: { type: 'string', description: 'the 64 hex digits sha256sum prints for the module now, before anything changes' },
    module_dirty: { type: 'boolean', description: 'git status --porcelain prints a line for the module (changed or untracked)' },
    functions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'kind', 'public', 'has_contracts', 'io'],
        properties: {
          name: { type: 'string', description: 'the name, Class.method for a method' },
          kind: { type: 'string', enum: ['function', 'class', 'method'] },
          public: { type: 'boolean', description: 'the name does not start with an underscore' },
          has_contracts: { type: 'boolean', description: 'icontract @require, @ensure or @invariant is on it now' },
          io: { type: 'boolean', description: 'reads or writes files, imports at run time, runs a process, reads the clock, randomness or the environment, or touches the network' },
          io_detail: { type: 'string', description: 'what I/O it does, if any' },
          callers: { ...LIST, description: 'file:function of each caller in this repository' },
          tests: { ...LIST, description: 'test functions that exercise it now' },
        },
      },
    },
    test_files: { ...LIST, description: 'test files that exercise the module now' },
    notes: { type: 'string', description: 'anything a later phase must know' },
  },
}

const PIN = {
  type: 'object',
  required: ['test_file', 'tests', 'passed', 'output', 'module_sha256_after', 'pinned_sha256'],
  properties: {
    test_file: { type: 'string', description: 'the file the pinned tests are in' },
    tests: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'function', 'pins'],
        properties: {
          name: { type: 'string' },
          function: { type: 'string' },
          pins: { type: 'string', description: 'the behaviour it records, in one line' },
        },
      },
    },
    command: { type: 'string', description: 'the command that ran them' },
    passed: { type: 'boolean', description: 'true only if every pinned test passed on the unchanged module' },
    output: { type: 'string', description: 'the last lines of the test run, verbatim' },
    module_sha256_after: { type: 'string', description: 'the 64 hex digits sha256sum prints for the module after the pinned tests pass' },
    pinned_sha256: { type: 'string', description: 'the 64 hex digits sha256sum prints for test_file after the pinned tests pass' },
    suspected_bugs: {
      type: 'array',
      items: {
        type: 'object',
        required: ['function', 'input', 'actual', 'why'],
        properties: {
          function: { type: 'string' },
          input: { type: 'string' },
          actual: { type: 'string', description: 'what the code does now, as pinned' },
          why: { type: 'string', description: 'why it looks wrong' },
        },
      },
    },
  },
}

const CONTRACT = {
  type: 'object',
  required: ['changed', 'files', 'functions', 'decisions', 'pinned_passed', 'output', 'note'],
  properties: {
    changed: { type: 'boolean' },
    files: LIST,
    functions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'requires', 'ensures'],
        properties: {
          name: { type: 'string' },
          requires: { type: 'integer', description: 'number of preconditions' },
          ensures: { type: 'integer', description: 'number of postconditions' },
          invariants: { type: 'integer', description: 'number of class invariants' },
        },
      },
    },
    moved_io: { ...LIST, description: 'each piece of I/O moved into a private function, and from where' },
    decisions: {
      type: 'array',
      description: 'changes only a person may choose, left undone',
      items: {
        type: 'object',
        required: ['function', 'kind', 'why'],
        properties: {
          function: { type: 'string' },
          kind: {
            type: 'string',
            enum: ['public_io', 'narrower_input'],
            description: 'public_io: a public module-level function that does I/O itself; narrower_input: a precondition or type check that would reject an input the code accepts today',
          },
          why: { type: 'string', description: 'the I/O it does and the callers a rename would change, or the input it would reject and who passes it' },
        },
      },
    },
    pinned_passed: { type: 'boolean', description: 'every pinned test passes, unchanged, after the change' },
    checks: {
      type: 'object',
      properties: {
        test: { type: 'boolean', description: 'pixi run --locked test passed' },
        lint: { type: 'boolean', description: 'pixi run --locked lint passed' },
        typecheck: { type: 'boolean', description: 'pixi run --locked typecheck passed' },
      },
    },
    output: { type: 'string', description: 'the decisive lines of each failing run, verbatim, or the pinned run if all passed' },
    note: { type: 'string', description: 'what changed and why' },
  },
}

const PROOF = {
  type: 'object',
  required: ['exit_code', 'refused', 'targets', 'counterexamples', 'uncontracted', 'problems', 'set_aside', 'verdicts', 'gate_output', 'pinned_passed', 'pinned_sha256'],
  properties: {
    exit_code: { type: 'integer', description: 'the exit status of the gate command; -1 if it was not run' },
    refused: { type: 'boolean', description: 'true if the gate checked nothing because it refused the module or an option' },
    targets: { type: 'integer', description: 'how many contracted functions and classes of the module the gate says it checked; 0 if it names none' },
    counterexamples: {
      type: 'array',
      items: {
        type: 'object',
        required: ['function', 'input', 'violated'],
        properties: {
          function: { type: 'string', description: 'qualified name of a function of the module' },
          input: { type: 'string', description: "CrossHair's counterexample input, verbatim, or none" },
          violated: { type: 'string', description: 'the condition it violates, verbatim, or Unable to meet precondition at file:line' },
        },
      },
    },
    uncontracted: { ...LIST, description: "functions of the module the gate's first check reported without contracts" },
    problems: {
      ...LIST,
      description:
        'every other "gate:" line that fails the gate, verbatim: a public coroutine, a pydantic model with contracts, a "# crosshair:" directive, an unproven entry the gate calls stale or misnamed, a target CrossHair found nothing to check in, a precondition no input can meet; not a counterexample, not a function without contracts, not a set-aside line, not the summary lines',
    },
    set_aside: {
      type: 'array',
      description: 'one entry per line "gate: not proven, contracts checked at run time only: <name>: <reason>": the functions listed under [tool.beethoven-qa.unproven], which the gate did not check as targets',
      items: {
        type: 'object',
        required: ['name', 'reason'],
        properties: {
          name: { type: 'string', description: 'the qualified name, as the line prints it' },
          reason: { type: 'string', description: 'the reason, verbatim from the line' },
        },
      },
    },
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['condition', 'verdict'],
        properties: {
          condition: { type: 'string', description: 'file:line of the condition, as CrossHair prints it' },
          verdict: { type: 'string', description: 'verbatim: Confirmed over all paths., Not confirmed., Unable to meet precondition., or the error line' },
        },
      },
    },
    gate_output: { type: 'string', description: 'the decisive lines of the gate output, verbatim' },
    pinned_passed: { type: 'boolean', description: 'every test in the pinned file passed' },
    pinned_output: { type: 'string', description: 'the failing pinned tests and their decisive lines, verbatim, if any failed' },
    pinned_sha256: { type: 'string', description: 'the 64 hex digits sha256sum prints for the pinned test file now' },
  },
}

const REPAIR = {
  type: 'object',
  required: ['changed', 'files', 'note', 'findings', 'preconditions'],
  properties: {
    changed: { type: 'boolean' },
    files: LIST,
    note: { type: 'string', description: 'per counterexample: was the code wrong or the contract, and what changed' },
    preconditions: {
      type: 'array',
      description: 'every @require this repair added or strengthened',
      items: {
        type: 'object',
        required: ['function', 'precondition', 'callers'],
        properties: {
          function: { type: 'string' },
          precondition: { type: 'string', description: 'the condition and its sentence, as written' },
          callers: { type: 'string', description: 'each caller checked (file:function) and why none of them passes what it excludes' },
        },
      },
    },
    findings: {
      type: 'array',
      description: 'real bugs in the code, each fixed only together with a test',
      items: {
        type: 'object',
        required: ['function', 'input', 'actual', 'expected', 'test'],
        properties: {
          function: { type: 'string' },
          input: { type: 'string' },
          actual: { type: 'string', description: 'what the code did before the fix' },
          expected: { type: 'string', description: 'what it should do, and why' },
          test: { type: 'string', description: 'file::test that failed before the fix and passes after it' },
        },
      },
    },
  },
}

const SKEPTIC = {
  type: 'object',
  required: ['claims'],
  properties: {
    claims: {
      type: 'array',
      items: {
        type: 'object',
        required: ['function', 'contract', 'kind', 'argument'],
        properties: {
          function: { type: 'string' },
          contract: { type: 'string', description: 'the contract exactly as written in the source: its lambda and its description' },
          kind: { type: 'string', enum: ['vacuous', 'restates_code', 'misses_failure'] },
          argument: { type: 'string', description: 'why, in plain words' },
          example: {
            type: 'object',
            required: ['witness', 'consequence'],
            properties: {
              witness: { type: 'string', description: 'a concrete input, or a concrete wrong body, that the contracts accept though they should not' },
              consequence: { type: 'string', description: 'what goes wrong for a caller' },
            },
          },
        },
      },
    },
  },
}

const REPORT = {
  type: 'object',
  required: ['functions', 'summary'],
  properties: {
    functions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'requires', 'ensures'],
        properties: {
          name: { type: 'string' },
          requires: { ...LIST, description: 'each precondition in plain words' },
          ensures: { ...LIST, description: 'each postcondition and invariant in plain words' },
          bound: { type: 'string', description: 'the declared symbolic bound' },
        },
      },
    },
    summary: { type: 'string', description: 'what the module now promises, what was found, and what a person still has to decide' },
  },
}

// What the bar asks of a contracted module, as measured in beethoven-qa (beethoven_qa/contracts.py
// and gate.py). Spelled out here because the plugin serves repositories whose own instructions may
// not repeat it.
const CONVENTIONS =
  'The bar: import require, ensure and invariant from beethoven_qa.contracts (only a file that must stand ' +
  'alone before the package is importable imports icontract directly). Put @beartype outermost and the ' +
  'icontract decorators beneath it: CrossHair still reads the contracts through the beartype wrapper, and a ' +
  'wrong-typed argument then fails as a beartype violation instead of a TypeError inside a contract lambda. ' +
  "A contract lambda's parameter names must match the function's (result is the return value). CrossHair " +
  'runs every contracted function with symbolic arguments, so a contracted function must be pure: anything ' +
  'that reads files, imports modules, runs a process or touches the network stays private (leading ' +
  'underscore) and uncontracted. Z3 decides linear integer arithmetic; a product of two unknowns it may not, ' +
  'so prefer linear logic where a proof must be total. Keep path explosion bounded: concrete loop bounds, I/O ' +
  'concretised at the edges, pure logic verified symbolically. Types in layers: pydantic models where ' +
  'untrusted data enters (files, the scheduler, LLM output, the network), @beartype on internal hot paths, ' +
  "typeguard only in tests (conftest's import hook). A subclass only weakens preconditions and strengthens " +
  'postconditions. A contract states what the function promises its callers, not a copy of its body. The gate ' +
  '(`pixi run --locked symbolic`; for one module `pixi run --locked python -m beethoven_qa gate --module ' +
  '<dotted name>`) fails a public module-level function without contracts and applies the ' +
  'per_condition_timeout under [tool.beethoven-qa] in pyproject.toml; that bound and the exempt list are not ' +
  'yours to change, and vendor/beethoven-qa/ is never edited by hand.'

// The moves that make a gate green without making the code right (the definition-of-done skill's
// "Never done to get green"), spelled out for the same reason as the bar above.
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
  "failure, say which move it would take, and fix the rest; it is a person's decision."

const SHA256 = /^[0-9a-f]{64}$/
const digest = (s) => String(s || '').trim().toLowerCase().split(/\s+/)[0]

// The gate's exit status decides, not the agent's reading of it, and a gate that checked nothing
// in the module proved nothing.
function isProven(proof) {
  return !proof.refused && proof.exit_code === 0 && proof.targets > 0
}

// The module is named by the person, never guessed: a default would improve some other file and
// report it as this one.
function moduleProblem(a) {
  if (!a || typeof a.module !== 'string' || a.module.length === 0) {
    return 'args.module is missing; pass {module: "src/<package>/<name>.py"}'
  }
  const parts = a.module.split('/')
  if (a.module.startsWith('/') || a.module.includes('\\') || parts.some((p) => p === '' || p === '.' || p === '..')) {
    return `args.module ${JSON.stringify(a.module)} is not a relative path inside this checkout`
  }
  if (!a.module.endsWith('.py')) return `args.module ${JSON.stringify(a.module)} is not a Python file`
  return null
}

// The dotted name an agent derived must name this file and no other: pkg.sub.name is
// .../pkg/sub/name.py or .../pkg/sub/name/__init__.py, whatever the source directory is called.
function dottedNames(module, dotted) {
  if (typeof dotted !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/.test(dotted)) return false
  const rel = dotted.split('.').join('/')
  return [`${rel}.py`, `${rel}/__init__.py`].some((tail) => module === tail || module.endsWith(`/${tail}`))
}

// Agents list things in no promised order; sorting makes "the same as last round" a property of
// the set, not of the listing.
function proofKey(proof) {
  const ces = proof.counterexamples.map((c) => [c.function, c.input, c.violated]).sort()
  return JSON.stringify([ces, [...proof.uncontracted].sort(), [...(proof.problems || [])].sort(), proof.pinned_passed])
}

// Two skeptics quote the same contract with different spacing; the key ignores that. The separator
// is a unit separator written as a hex escape: tools/check_workflows.mjs refuses every unicode
// escape, since one could spell a name it reads as written.
function claimKey(c) {
  return `${c.function}\x1f${c.contract.replace(/\s+/g, ' ').trim()}`
}

// What the gate printed as set aside, as a list a person can read; the gate passes with these
// unproven, so a proof is never reported without them.
function setAside(proof) {
  return (proof.set_aside || []).map((s) => ({ name: String(s.name), reason: String(s.reason) }))
}

// A repair may change the pinned file only through a correction it names: a finding whose test is
// in that file.
function namesPinnedAssertion(repair, pinFile) {
  return (repair.findings || []).some((f) => String(f.test || '').split('::')[0] === pinFile)
}

const problem = moduleProblem(args)
if (problem) {
  log(`improve-module: ${problem}; nothing was run`)
  return { error: problem }
}
const module = args.module
let maxRounds = 3
if (args.maxRounds !== undefined) {
  if (Number.isInteger(args.maxRounds) && args.maxRounds >= 0) maxRounds = args.maxRounds
  else log(`maxRounds ${JSON.stringify(args.maxRounds)} is not a whole number of rounds; using ${maxRounds}`)
}
const rounds = []
const findings = []
const outcome = (fields) => ({ module, proven: false, rounds, findings, vacuity: null, contracts: null, ...fields })

if (maxRounds === 0) {
  log('maxRounds is 0: no agent runs; this is the syntax check of the script')
  return outcome({ stopped: 'maxRounds is 0' })
}

// --- Read ------------------------------------------------------------------------------------
phase('Read')
const map = await agent(
  `Map the Python module ${module} in this checkout. Change no file (installing the environment into .pixi/ ` +
    'is not a change). If the checkout has no .pixi/ environment yet, run `pixi install --locked` first (never `pixi lock`, `pixi update`, `pixi add` or a ' +
    'plain `pixi install`, which may re-solve the lock). Read [tool.beethoven-qa] in pyproject.toml: report ' +
    `the package it names, set found=false if ${module} does not exist or is not a module of that package, ` +
    'and say whether it is listed in exempt. Report its dotted name: its path below the source directory the ' +
    'table names (src; "src" when not set) without ".py", with "/" as "." (src/pkg/sub/name.py is ' +
    'pkg.sub.name; an __init__.py names its package). Before anything else changes, run ' +
    `\`sha256sum ${module}\` and report the 64 hex digits, and report whether ` +
    `\`git status --porcelain -- ${module}\` prints a line (uncommitted or untracked). ` +
    'For every module-level function, class and public method: its name, whether it is ' +
    'public, whether it carries icontract contracts now, whether it does I/O (reads or writes files, imports ' +
    'at run time, runs a process, reads the clock, randomness or the environment, touches the network) and ' +
    'what, its callers in this repository (file:function), and the tests that exercise it. List the test ' +
    'files that exercise the module.',
  { label: `map ${module}`, phase: 'Read', schema: MAP },
)
if (!map) {
  log('the map agent returned nothing; stopping')
  return outcome({ stopped: 'the Read phase returned nothing' })
}
if (!map.found) {
  log(`${module} is not a module of the package the gate checks: ${map.notes}`)
  return outcome({ error: `${module} is not a module of the package [tool.beethoven-qa] names`, stopped: 'the module was not found in the package' })
}
// The module as it was when the run started; the Pin phase is held to it, whatever git says.
const startSha = digest(map.module_sha256)
if (!SHA256.test(startSha)) {
  log(`the map agent reported no sha256 of ${module} (${JSON.stringify(map.module_sha256)}); stopping before anything changes, since a later change could not be told from the module as it was`)
  return outcome({ stopped: 'no sha256 of the module at the start of the run' })
}
const ioCount = map.functions.filter((f) => f.io).length
log(`${module}: ${map.functions.length} function(s) and class(es), ${ioCount} doing I/O, ${map.test_files.length} test file(s); sha256 ${startSha} at the start`)
if (map.module_dirty) {
  log(`${module} had uncommitted changes (or was untracked) before this run; they are the person's, not this run's, and they stay mixed with what this run changes`)
}
if (map.exempt) {
  log(`${module} is listed in exempt under [tool.beethoven-qa], and gate --module refuses an exempt module, so nothing could be proven; stopping before anything changes (whether it stays exempt is a person's call, not this workflow's)`)
  return outcome({ error: `${module} is exempt`, stopped: 'the module is exempt' })
}
const dotted = map.dotted
if (!dottedNames(module, dotted)) {
  log(`the map agent named ${JSON.stringify(dotted)} as the dotted name of ${module}, which it is not; stopping before anything changes`)
  return outcome({ error: `no dotted name for ${module}`, stopped: 'the Read phase gave no dotted name for the module' })
}
// The map the later agents read, without the digest the Pin phase is held to.
const shown = { ...map }
delete shown.module_sha256
const mapText = JSON.stringify(shown, null, 1)

// --- Pin -------------------------------------------------------------------------------------
phase('Pin')
const parts = module.split('/')
const base = parts[parts.length - 1].replace(/\.py$/, '')
const stem = base === '__init__' && parts.length > 1 ? parts[parts.length - 2] : base
const pinFile = `tests/test_${stem}_pinned.py`
const pin = await agent(
  `Write characterisation tests that pin what ${module} does now, before anything in it changes. ` +
    `The map of the module:\n${mapText}\n\n` +
    `Put them in ${pinFile} (or, if the repository keeps its tests elsewhere, the same file name there; ` +
    'if the file exists, add to it and keep what is in it), and change no other file. Pin what the code ' +
    'does, not what it ought to do: for each public function and method, its results on ordinary inputs and on the ' +
    'edges its code branches on (empty, zero, one, the boundaries), and the exceptions it raises today. ' +
    "Pin I/O with pytest's tmp_path or with values passed in, never by touching anything outside the " +
    "test's own temporary directory. The tests run under typeguard's import hook (conftest.py). Run them " +
    'with `pixi run --locked pytest -q <the file>` and make them pass on the unchanged module by fixing the ' +
    `tests, never the module; then run \`sha256sum ${module}\` and \`sha256sum <the file>\` and report the ` +
    '64 hex digits of each. ' +
    'Where a behaviour looks like a bug, pin it as it is and list it under suspected_bugs: it is decided ' +
    'later, with a test. Never run `pixi lock`, `pixi update`, `pixi add` or a plain `pixi install`. Do not commit.',
  { label: `pin ${module}`, phase: 'Pin', schema: PIN },
)
if (!pin) {
  log('the pin agent returned nothing; stopping before anything in the module changes')
  return outcome({ stopped: 'the Pin phase returned nothing' })
}
const pinSha = digest(pin.module_sha256_after)
if (pinSha !== startSha) {
  // Nothing is restored: the module as the run found it may hold the person's own uncommitted work,
  // which git cannot give back, so the person compares before keeping either.
  const what = SHA256.test(pinSha) ? `sha256 ${pinSha} now` : `no sha256 after the Pin phase (${JSON.stringify(pin.module_sha256_after)})`
  log(`the Pin phase changed ${module}, or could not show it did not: sha256 ${startSha} at the start of this run, ${what}; stopping with nothing restored: a person compares the module with what it was before keeping either`)
  return outcome({ stopped: 'the module is not as it was at the start of the run after the Pin phase', pinned: pin.test_file, module_sha256: { start: startSha, after_pin: pinSha } })
}
if (!pin.passed || pin.tests.length === 0) {
  log(`the pinned tests do not pass on the unchanged module (or none were written); stopping:\n${pin.output}`)
  return outcome({ stopped: 'no passing pin of the current behaviour', pinned: pin.test_file })
}
// The pinned file as it passed; every proving round is held to it, and only a repair that names
// the pinned assertion it corrected may move it.
let pinnedSha = digest(pin.pinned_sha256)
if (!SHA256.test(pinnedSha)) {
  log(`the pin agent reported no sha256 of ${pin.test_file} (${JSON.stringify(pin.pinned_sha256)}); stopping before anything in the module changes, since a later change to the pinned tests could not be told from the tests as they passed`)
  return outcome({ stopped: 'no sha256 of the pinned tests after the Pin phase', pinned: pin.test_file })
}
log(`pinned ${pin.tests.length} behaviour(s) in ${pin.test_file} (sha256 ${pinnedSha}); ${(pin.suspected_bugs || []).length} suspected bug(s) pinned as they are`)

// --- Contract --------------------------------------------------------------------------------
phase('Contract')
const suspected = (pin.suspected_bugs || []).map((b) => `- ${b.function}(${b.input}) gives ${b.actual}: ${b.why}`).join('\n')
const contract = await agent(
  `Bring ${module} up to the definition of done without changing what it does. The tests in ` +
    `${pin.test_file} pin what it does now; they must still pass, unchanged, when you finish.\n\n` +
    `The map of the module:\n${mapText}\n\n${CONVENTIONS}\n\n` +
    'Do this, in this checkout: (1) where a function mixes deciding with I/O, move the deciding into a pure ' +
    'function that carries the contracts, and keep the reading, writing, importing and process-starting ' +
    'in a private function without contracts, keeping every public name, signature and result the same for ' +
    'its callers; (2) put @require and @ensure on every public function (and @invariant on classes), saying ' +
    'what it promises; (3) layer the types: @beartype on internal functions, pydantic models where untrusted ' +
    'data enters; (4) run `pixi run --locked pytest -q ' + pin.test_file + '`, then `pixi run --locked test`, ' +
    '`pixi run --locked lint` and `pixi run --locked typecheck`, and fix what your change broke, never a ' +
    `pinned test.\n\n${NEVER}\n\nTwo things are a ` +
    "person's to choose, so leave them undone and list them under decisions: a public module-level function " +
    'that itself does I/O (it cannot carry contracts CrossHair may run and cannot pass the gate without ' +
    'them, unless a person names it with its reason under [tool.beethoven-qa.unproven]; kind public_io, ' +
    'with the I/O and the callers a rename would change), and a precondition or type ' +
    'check that would reject an input the code accepts today, which is a change of behaviour even where the ' +
    'input looks wrong (kind narrower_input, with the input and who passes it). Never add the module to ' +
    'exempt and do not commit.' +
    (suspected ? `\n\nBehaviours pinned as they are though they look wrong (leave them; they are decided later):\n${suspected}` : ''),
  { label: `contract ${module}`, phase: 'Contract', schema: CONTRACT },
)
if (!contract) {
  log('the contract agent returned nothing; stopping (whatever it changed is left in the checkout for a person)')
  return outcome({ stopped: 'the Contract phase returned nothing', pinned: pin.test_file })
}
if (!contract.pinned_passed) {
  log(`the pinned tests fail after the contract change, so behaviour changed; stopping, the change is left in the checkout for a person:\n${contract.output}`)
  return outcome({ stopped: 'the contract change altered pinned behaviour', pinned: pin.test_file, decisions: contract.decisions })
}
const awaiting = new Set(contract.decisions.filter((d) => d.kind === 'public_io').map((d) => d.function))
log(`contracts on ${contract.functions.length} public function(s); ${awaiting.size} left for a person's decision; ${contract.note}`)
const checks = contract.checks || {}
for (const name of ['test', 'lint', 'typecheck']) {
  if (checks[name] === false) log(`after the contract change, pixi run --locked ${name} fails; the gate will show it`)
}

// --- Prove -----------------------------------------------------------------------------------
// Done means both: the gate proves the module, and the pinned tests still pass, so a repair that
// buys a proof by changing behaviour is caught in the next round rather than reported as success.
let finished = false
let stopped = null
let refusal = null
let lastKey = null
let lastProof = null
let pinnedMayChange = false
// Set when the run stops because the pinned file moved without a named correction: what its tests
// passed then is not the pinned behaviour, so the last proof's pinned verdict is reported as none.
let pinnedMoved = false
const preconditions = []
for (let round = 1; round <= maxRounds; round++) {
  phase('Prove')
  const proof = await agent(
    `Run \`pixi run --locked python -m beethoven_qa gate --module ${dotted} --report-all\` in this checkout: ` +
      `the gate restricted to ${module}, at the per_condition_timeout the repository declares. Its first ` +
      'check lists the public functions of the module without contracts, its second runs CrossHair on the ' +
      "module's contracted functions and classes whatever the rest of the package looks like, and " +
      "every run prints every condition's verdict and fails a precondition no input can meet " +
      '(--report-all is accepted and changes nothing). Report its exit status. It exits 2 with a message, ' +
      'having checked nothing, when the ' +
      'module is not part of the package, lies outside it or is exempt: report refused=true then, and also ' +
      'if the command does not know --module; a module that fails on import is refused too, ' +
      `so then also run \`pixi run --locked python -c "import ${dotted}"\` and add its last lines to ` +
      'gate_output. List every counterexample with the function, ' +
      'the input and the violated condition, verbatim; each condition reported "Unable to meet ' +
      'precondition.", with input "none" and violated "Unable to meet precondition at <file:line>"; every ' +
      "function the first check reported without contracts; every condition's verdict; how many " +
      'contracted functions and classes the gate says it checked; under problems, verbatim, every other ' +
      '"gate:" line that fails the gate (a public coroutine, a pydantic model with contracts, a "# crosshair:" ' +
      'directive, an unproven entry the gate calls stale or misnamed, a target CrossHair found nothing to ' +
      'check in, a precondition no input can meet); and under set_aside each line "gate: not proven, ' +
      'contracts checked at run time only: <name>: <reason>", with its name and reason. Then run ' +
      `\`pixi run --locked pytest -q ${pin.test_file}\` and report whether every pinned test passed, with ` +
      `the failing ones verbatim, and run \`sha256sum ${pin.test_file}\` and report the 64 hex digits. ` +
      'Change no file, and never run `pixi lock`, `pixi update`, `pixi add` or a plain `pixi install`.',
    { label: `prove ${module} round ${round}`, phase: 'Prove', schema: PROOF },
  )
  if (!proof) {
    stopped = `the prove agent returned nothing in round ${round}` + (round > 1 ? '; the last repair has not been proven' : '')
    log(`${stopped}; stopping`)
    break
  }
  if (proof.refused) {
    refusal = `the gate did not check ${module}: ${proof.gate_output || 'it refused the module'}`
    stopped = refusal
    log(`round ${round}: ${refusal}`)
    break
  }
  lastProof = proof
  const provenNow = isProven(proof)
  const problems = proof.problems || []
  rounds.push({
    round,
    exit_code: proof.exit_code,
    targets: proof.targets,
    counterexamples: proof.counterexamples.length,
    uncontracted: proof.uncontracted.length,
    problems: problems.length,
    pinned_passed: proof.pinned_passed,
  })
  // The pinned file is read by an agent that was never told the digit string it is compared with.
  const nowSha = digest(proof.pinned_sha256)
  if (!SHA256.test(nowSha)) {
    stopped = `the prove agent reported no sha256 of ${pin.test_file} (${JSON.stringify(proof.pinned_sha256)}), so whether it is as it passed in the Pin phase is not known; stopping for a person`
    log(`round ${round}: ${stopped}`)
    break
  }
  if (nowSha !== pinnedSha) {
    if (!pinnedMayChange) {
      const who =
        round === 1
          ? 'the Contract phase, which may not change it'
          : `the repair in round ${round - 1}, which named no pinned assertion it corrected`
      stopped = `${pin.test_file} is not as it passed in the Pin phase (sha256 ${pinnedSha} then, ${nowSha} now) after ${who}; what its tests pass now is not the pinned behaviour, so the change is left in the checkout for a person`
      pinnedMoved = true
      log(`round ${round}: ${stopped}`)
      break
    }
    log(`round ${round}: ${pin.test_file} changed with the repair in round ${round - 1}, which named the pinned assertion it corrected; sha256 ${nowSha} now`)
    pinnedSha = nowSha
  }
  pinnedMayChange = false
  if (provenNow && proof.pinned_passed) {
    finished = true
    const aside = setAside(proof)
    log(
      `round ${round}: ${module} has no counterexample within its declared bound, and its pinned behaviour holds` +
        (aside.length ? `; ${aside.length} function(s) set aside under [tool.beethoven-qa.unproven] and not proven: ${aside.map((s) => `${s.name} (${s.reason})`).join('; ')}` : ''),
    )
    break
  }
  if (!proof.pinned_passed) log(`round ${round}: pinned tests fail, so behaviour changed since the Pin phase`)
  const open = proof.uncontracted.filter((name) => !awaiting.has(name) && !awaiting.has(name.split('.').pop()))
  if (!provenNow && proof.counterexamples.length === 0 && proof.uncontracted.length === 0 && problems.length === 0) {
    stopped =
      proof.targets <= 0
        ? `the gate checked no contracted function or class in ${module}, so nothing was proven`
        : `the gate failed on ${module} but named no counterexample, no function without contracts and no other problem; its output is returned for a person`
    log(`round ${round}: ${stopped}`)
    break
  }
  if (proof.pinned_passed && proof.counterexamples.length === 0 && open.length === 0 && problems.length === 0) {
    // The gate's second check ran on this module whatever its first check found, so the contracts
    // that are here were checked; only the person's decisions are left. A problem the gate reports
    // beside them (a coroutine, a directive, a stale entry) is not theirs, and goes to a repair.
    stopped =
      proof.targets > 0
        ? `CrossHair found no counterexample in the ${proof.targets} contracted function(s) and class(es) of ${module} ` +
          "within the declared bound; only the public I/O functions left for a person's decision keep it from the gate"
        : `no function or class of ${module} carries a contract CrossHair could check, and the public I/O functions ` +
          "left for a person's decision keep it from the gate"
    log(`round ${round}: ${stopped}`)
    break
  }
  const key = proofKey(proof)
  if (key === lastKey) {
    // The key holds every finding of the gate and the pinned verdict; name what this round had.
    const same = [
      proof.counterexamples.length ? 'counterexamples' : '',
      proof.uncontracted.length ? 'functions without contracts' : '',
      problems.length ? 'gate problems' : '',
      proof.pinned_passed ? '' : 'failing pinned tests',
    ].filter(Boolean)
    stopped = `the same ${same.length > 1 ? `${same.slice(0, -1).join(', ')} and ${same[same.length - 1]}` : same[0]} two rounds running`
    log(`round ${round}: ${stopped}; stopping`)
    break
  }
  lastKey = key
  // A repair nothing proves afterwards would be reported as unproven on stale counterexamples.
  if (round === maxRounds) break
  const repair = await agent(
    `The module ${module} is not done yet.\n` +
      proof.counterexamples.map((c) => `- ${c.function}: input ${c.input} violates ${c.violated}`).join('\n') +
      (open.length ? `\nWithout contracts: ${open.join(', ')}` : '') +
      (problems.length ? `\nThe gate also reports:\n${problems.map((p) => `- ${p}`).join('\n')}` : '') +
      (proof.pinned_passed ? '' : `\nPinned tests that fail now:\n${proof.pinned_output || '(no output reported)'}`) +
      `\n\nGate output:\n${proof.gate_output}\n\n${CONVENTIONS}\n\n${NEVER}\n\n` +
      'For each counterexample decide whether the code or the contract is wrong, and fix that one thing ' +
      `minimally in ${module} or its tests. A line the gate reports beside the counterexamples (a public ` +
      'coroutine, contracts on a pydantic model, a `# crosshair:` directive, a target CrossHair found nothing ' +
      'to check in) is fixed in the module the way the line says; an entry under [tool.beethoven-qa.unproven] ' +
      "the gate calls stale or misnamed is the person's to remove, so leave it and say so. " +
      'Before deciding, call the function with the input in a test and ' +
      'watch it fail. A report that does not reproduce concretely is a fault of the tool: record the input, ' +
      'the line and the CrossHair version in your note, and never change the code, loosen a contract or lower ' +
      "the bound for it; only a precondition that states the callers' real domain may exclude its input. An " +
      'entry "Unable to meet precondition" means CrossHair found no input that meets the function\'s ' +
      'preconditions within the bound: if they contradict each other, the function refuses every call ' +
      'today, so record a finding and correct them to what the callers really pass; if they are only beyond ' +
      'the solver, state the same domain in a form it can meet. A failing pinned test means behaviour ' +
      'changed: restore it, unless that test pinned a real bug. If the code is wrong (a real bug), first ' +
      'write a test that fails on it, then fix the code, and record a finding with the input, what the code ' +
      `did, what it should do and the test; if a test in ${pin.test_file} pinned the wrong behaviour, change ` +
      `that one assertion to the expected value, name it as the finding's test (${pin.test_file}::<test>), and ` +
      `never delete a pinned test; ${pin.test_file} changes in no other way. Then ` +
      `run \`pixi run --locked pytest -q ${pin.test_file}\` and the tests you touched.` +
      (awaiting.size ? ` Leave these public I/O functions alone, they wait on a person: ${[...awaiting].join(', ')}.` : '') +
      ' Do not commit. Report what changed and why, per counterexample, and every precondition you added or ' +
      'strengthened with the callers you checked.',
    { label: `repair ${module} round ${round}`, phase: 'Prove', schema: REPAIR },
  )
  if (!repair) {
    stopped = `the repair agent returned nothing in round ${round}; what it changed has not been proven`
    log(`${stopped}; stopping`)
    break
  }
  findings.push(...repair.findings)
  const added = repair.preconditions || []
  preconditions.push(...added.map((p) => ({ round, ...p })))
  log(`round ${round}: ${repair.changed ? `${repair.files.length} file(s) changed` : 'nothing changed'}, ${repair.findings.length} bug(s) found: ${repair.note}`)
  if (added.length) {
    log(`round ${round}: ${added.length} precondition(s) added or strengthened; a person checks each against its callers: ${added.map((p) => `${p.function}: ${p.precondition}`).join('; ')}`)
  }
  pinnedMayChange = namesPinnedAssertion(repair, pin.test_file)
  // Proving unchanged code again would only repeat this round.
  if (!repair.changed) {
    stopped = `the repair in round ${round} changed nothing`
    break
  }
}
if (!finished && !stopped) {
  stopped = `not done within ${maxRounds} round(s)`
  log(`${module}: ${stopped}`)
}
if (refusal) {
  // The gate checked nothing, so there is no proof for the skeptics to weigh or the reporter to
  // write up; what the Pin and Contract phases changed stays in the checkout.
  log(`${module}: ${refusal}; the pinned tests and the contract change are left in the checkout, unproven, for a person`)
  return outcome({ error: refusal, stopped, pinned: pin.test_file, decisions: contract.decisions, preconditions })
}
const proven = Boolean(lastProof && isProven(lastProof))
const pinnedPassed = lastProof && !pinnedMoved ? lastProof.pinned_passed : null
const verdicts = lastProof ? lastProof.verdicts || [] : []
const aside = lastProof ? setAside(lastProof) : []

// --- Vacuity ---------------------------------------------------------------------------------
// A proof says the code keeps its contracts; it says nothing about whether the contracts are worth
// keeping. Two skeptics read independently and in parallel, from different starting points, and a
// claim counts only if both make it or one backs it with a concrete witness. They start from every
// condition's verdict in the last proving round (`--report-all`) and do not run the gate themselves:
// two gates side by side would run twice as many CrossHair processes as there are cores.
phase('Vacuity')
const verdictText = lastProof
  ? `Every condition's verdict in the last proving round (\`pixi run --locked python -m beethoven_qa gate --module ${dotted} --report-all\`):\n${JSON.stringify(verdicts, null, 1)}\n` +
    'A condition reported "Unable to meet precondition." admits no input CrossHair could find within the bound, so ' +
    'nothing was checked behind it: claim it as vacuous with that line as the witness. "Confirmed over all paths." ' +
    'is a total proof of what the condition says, not of whether it says enough.'
  : 'The gate reported no verdict on this module in this run.'
const ANGLES = [
  'Start from each contract and look for what it fails to rule out: a concrete wrong body or input it would accept.',
  "Start from the callers in the map and the failure that would hurt them most, then check whether any contract catches it.",
]
const skeptics = await parallel(
  ANGLES.map((angle, i) => () =>
    agent(
      `Read ${module} and its contracts; change no file. Try to show that a contract is vacuous (any ` +
        'implementation, even a wrong one, satisfies it), that it restates the code instead of stating what ' +
        'the caller is promised, or that it misses the failure that matters. ' +
        `${angle} Quote each contract exactly as written. Give a concrete witness whenever you can; claim ` +
        `nothing you cannot argue.\n${verdictText}\nThe map of the module:\n${mapText}`,
      { label: `skeptic ${i + 1} on ${module}`, phase: 'Vacuity', schema: SKEPTIC },
    ),
  ),
)
const answered = skeptics.filter(Boolean)
if (answered.length < skeptics.length) log(`${skeptics.length - answered.length} skeptic(s) returned nothing; only claims with a witness can count`)
const seen = new Map()
answered.forEach((s, i) => {
  for (const c of s.claims) {
    const k = claimKey(c)
    const entry = seen.get(k) || { ...c, raised_by: [] }
    if (!entry.raised_by.includes(i + 1)) entry.raised_by.push(i + 1)
    if (!entry.example && c.example) entry.example = c.example
    seen.set(k, entry)
  }
})
const claims = [...seen.values()]
const hasWitness = (c) => Boolean(c.example && c.example.witness && c.example.witness.trim())
const counted = claims.filter((c) => c.raised_by.length >= 2 || hasWitness(c))
const uncounted = claims.filter((c) => !(c.raised_by.length >= 2 || hasWitness(c)))
const vacuity = { skeptics: answered.length, raised: claims.length, counted, uncounted }
log(`vacuity: ${claims.length} claim(s) raised, ${counted.length} counted (both skeptics, or a witness), ${uncounted.length} kept as uncounted`)

// --- Report ----------------------------------------------------------------------------------
phase('Report')
const report = await agent(
  `Read ${module}; change no file. For every public function, write its preconditions and its ` +
    'postconditions and class invariants in plain words a reviewer can check against the intent, one line ' +
    'each, with its verdict from the list below ("confirmed over all paths", or "no counterexample within N s", ' +
    'N the declared bound; "proven" only for the first), and name the declared symbolic bound ' +
    '(per_condition_timeout under [tool.beethoven-qa] in pyproject.toml). Then a short summary for the person: ' +
    'what the module now promises, what was found, and what they still have to decide. No counterexample ' +
    `from the gate on this module: ${proven}; ` +
    (pinnedMoved
      ? `pinned tests ${lastProof.pinned_passed ? 'passed' : 'failed'} on a pinned file that changed without a named correction`
      : `pinned behaviour holds: ${pinnedPassed}`) +
    `${stopped ? `; stopped: ${stopped}` : ''}.\n` +
    `Each condition's verdict:\n${JSON.stringify(verdicts, null, 1)}\n` +
    'Functions set aside under [tool.beethoven-qa.unproven], which the gate did not check as targets: mark each of their ' +
    'conditions "not proven: contracts checked at run time only (<reason>)", never "no counterexample":\n' +
    `${JSON.stringify(aside, null, 1)}\n` +
    `Preconditions a repair added or strengthened (check each against its callers):\n${JSON.stringify(preconditions, null, 1)}\n` +
    `Bugs found and fixed with a test:\n${JSON.stringify(findings, null, 1)}\n` +
    `Contract weaknesses that counted:\n${JSON.stringify(counted, null, 1)}\n` +
    `Changes left for a person to choose:\n${JSON.stringify(contract.decisions, null, 1)}`,
  { label: `report ${module}`, phase: 'Report', schema: REPORT },
)
if (!report) log('the report agent returned nothing; the contracts are not summarised')

log(
  'nothing was committed: commit the pinned tests, the contract change and each fix with its test as separate commits, then run /beethoven-qa:gate-until-green before pushing' +
    (map.module_dirty ? `; ${module} already had uncommitted changes when this run started (sha256 ${startSha}), so look at its diff before committing it` : ''),
)
return outcome({
  proven,
  pinned_passed: pinnedPassed,
  stopped,
  pinned: pin.test_file,
  decisions: contract.decisions,
  counterexamples: lastProof ? lastProof.counterexamples : [],
  verdicts,
  set_aside: aside,
  preconditions,
  vacuity,
  contracts: report ? report.functions : null,
  summary: report ? report.summary : null,
})
