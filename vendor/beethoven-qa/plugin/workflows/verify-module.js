export const meta = {
  name: 'verify-module',
  description: 'The SpecPylot loop for one module: prove it with the gate restricted to that module (every condition\'s verdict), feed each counterexample to a repair agent, repeat until proven or stuck, then write the contracts out for a human',
  whenToUse: 'A module that already carries contracts must be proven within its declared bound; pass args {module: "src/<package>/<name>.py"}',
  phases: [
    { title: 'Prove', detail: 'pixi run --locked python -m beethoven_qa gate --module <its dotted name> --report-all' },
    { title: 'Repair', detail: 'one repair per counterexample round' },
    { title: 'Review', detail: 'the contracts in plain words, for the human reviewer' },
  ],
}
// Step 6 of the definition of done, "generate code + contracts, run the gate, feed the Z3
// counterexample back, iterate until proven; humans review the contracts, not raw lines", as a
// script, for any repository whose gate is the beethoven-qa gate. Written in beethoven-research on
// 2026-10-04 and moved into the beethoven-qa plugin at the split. Repairs are sequential (one editor
// in this checkout). A repair may change the code or the contract that is wrong; it never loosens a
// bound, removes a contract, narrows a precondition past what callers pass, or exempts the module to
// make the gate pass.
//
// The verdict is the gate's own on this one module (`gate --module`): the gate refuses a module
// that is not part of the package it checks, so a mistyped path or a file outside the package comes
// back as an error, never as proven; both of its checks run on the module whatever the rest of the
// package looks like; `--report-all` gives every condition's verdict and fails a condition whose
// preconditions CrossHair could not meet; and a module in which the gate checked nothing is not
// proven either. A function listed under [tool.beethoven-qa.unproven] passes the gate without
// being checked as a target, so the result, the log and the review name each such function with
// its reason: the reviewer must see which part of a proven module was never proven.
//
// args: { module: 'src/<package>/<name>.py', maxRounds?: number }
//       module is required: without it nothing runs and the result is { error }.
//       maxRounds, the proving rounds, defaults to 3 (a repair runs only between two proofs, so the
//       last round proves and does not repair); 0 runs no agent and is the syntax check of this file.

const PROOF = {
  type: 'object',
  required: ['dotted', 'exit_code', 'refused', 'targets', 'counterexamples', 'set_aside', 'verdicts', 'gate_output'],
  properties: {
    dotted: { type: 'string', description: 'the dotted module name passed to --module, empty if none could be derived' },
    exit_code: { type: 'integer', description: 'the exit status of the gate command; -1 if it was not run' },
    refused: {
      type: 'boolean',
      description: 'true if the gate checked nothing because it refused the module or an option, or the path lies outside the source directory of the package [tool.beethoven-qa] names',
    },
    targets: { type: 'integer', description: 'how many contracted functions and classes of the module the gate says it checked; 0 if it names none' },
    counterexamples: {
      type: 'array',
      items: {
        type: 'object',
        required: ['function', 'input', 'violated'],
        properties: {
          function: { type: 'string', description: 'qualified name of the function' },
          input: { type: 'string', description: "CrossHair's counterexample input, verbatim, or none" },
          violated: { type: 'string', description: 'the postcondition or invariant it violates, verbatim; or no contracts; or Unable to meet precondition at file:line' },
        },
      },
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
  },
}

const REPAIR = {
  type: 'object',
  required: ['changed', 'files', 'note', 'preconditions'],
  properties: {
    changed: { type: 'boolean' },
    files: { type: 'array', items: { type: 'string' } },
    note: { type: 'string', description: 'for each counterexample: was the code wrong or the contract, and what changed' },
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
  },
}

const REVIEW = {
  type: 'object',
  required: ['functions'],
  properties: {
    functions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'requires', 'ensures'],
        properties: {
          name: { type: 'string' },
          requires: { type: 'array', items: { type: 'string' }, description: 'each precondition in plain words' },
          ensures: { type: 'array', items: { type: 'string' }, description: 'each postcondition and invariant in plain words, with its verdict' },
          bound: { type: 'string', description: 'the declared symbolic bound, if any' },
        },
      },
    },
  },
}

// The moves that make a gate green without making the code right (the definition-of-done skill's
// "Never done to get green"), spelled out because the plugin serves repositories whose own
// instructions may not repeat them.
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

// The module is named by the person, never guessed: a default would prove some other file and
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

// Agents list counterexamples in no promised order; sorting makes "the same as last round" a
// property of the set, not of the listing.
function counterexampleKey(list) {
  return JSON.stringify(list.map((c) => [c.function, c.input, c.violated]).sort())
}

// The gate's exit status decides, not the agent's reading of it, and a gate that checked nothing
// in the module proved nothing.
function isProven(proof) {
  return !proof.refused && proof.exit_code === 0 && proof.targets > 0
}

function proveCommand(module, dotted) {
  const name = dotted
    ? `Its dotted name is ${dotted}.`
    : "Derive its dotted name first: read [tool.beethoven-qa] in pyproject.toml for the package and its source " +
      'directory (src; "src" when the table does not set it); the dotted name is the path below that ' +
      'directory without ".py", with "/" as "." (src/pkg/sub/name.py is pkg.sub.name; an __init__.py names ' +
      `its package). If ${module} does not lie below that directory, run nothing and report refused=true.`
  return (
    `Prove the module ${module} in this checkout with the gate restricted to it. ${name} Run ` +
    '`pixi run --locked python -m beethoven_qa gate --module <dotted name> --report-all` and report its exit ' +
    'status and the dotted name you passed. The gate checks only that module, at the per_condition_timeout ' +
    'the repository declares: its first check lists the public functions without contracts, its second runs ' +
    'CrossHair on the contracted functions and classes, and --report-all prints every condition\'s verdict ' +
    'and fails a condition whose preconditions CrossHair could not meet. It exits 2 with a message, having ' +
    'checked nothing, when the module is not part of the package, lies outside it or is exempt: report ' +
    'refused=true then, and also if the command does not know --module or --report-all; if it refused a file ' +
    'that does exist below the source directory, also run `pixi run --locked python -c "import <dotted name>"` ' +
    'and add its last lines to gate_output (a module that fails on import is refused too). List every ' +
    'counterexample with the function, the input and the violated condition, verbatim; each public function ' +
    'the first check lists, with input "none" and violated "no contracts"; and each condition reported ' +
    '"Unable to meet precondition.", with input "none" and violated "Unable to meet precondition at ' +
    '<file:line>". List every condition\'s verdict, and how many contracted functions and classes the gate ' +
    'says it checked. List under set_aside each line "gate: not proven, contracts checked at run time ' +
    'only: <name>: <reason>", with its name and reason: those functions are listed under ' +
    '[tool.beethoven-qa.unproven] and the gate did not check them as targets. Change no file, and never run `pixi ' +
    'lock`, `pixi update`, `pixi add` or a plain `pixi install`.'
  )
}

// What the gate printed as set aside, as a list a person can read; the gate passes with these
// unproven, so a proof is never reported without them.
function setAside(proof) {
  return (proof.set_aside || []).map((s) => ({ name: String(s.name), reason: String(s.reason) }))
}

const problem = moduleProblem(args)
if (problem) {
  log(`verify-module: ${problem}; nothing was run`)
  return { error: problem }
}
const module = args.module
let maxRounds = 3
if (args.maxRounds !== undefined) {
  if (Number.isInteger(args.maxRounds) && args.maxRounds >= 0) maxRounds = args.maxRounds
  else log(`maxRounds ${JSON.stringify(args.maxRounds)} is not a whole number of rounds; using ${maxRounds}`)
}
const rounds = []
const repairs = []
let dotted = ''
let lastKey = null
let lastProof = null
let stopped = null
let proven = false

for (let round = 1; round <= maxRounds; round++) {
  phase('Prove')
  const proof = await agent(proveCommand(module, dotted), {
    label: `prove ${module} round ${round}`,
    phase: 'Prove',
    schema: PROOF,
  })
  if (!proof) {
    stopped = `the prove agent returned nothing in round ${round}` + (round > 1 ? '; the last repair has not been proven' : '')
    log(`${stopped}; stopping`)
    break
  }
  if (proof.refused) {
    const error = `the gate did not check ${module}: ${proof.gate_output || 'it refused the module'}`
    log(`round ${round}: ${error}`)
    return { module, error, proven: false, rounds, repairs }
  }
  if (!dottedNames(module, proof.dotted)) {
    const error = `the gate was run on ${JSON.stringify(proof.dotted)}, which is not the dotted name of ${module}`
    log(`round ${round}: ${error}`)
    return { module, error, proven: false, rounds, repairs }
  }
  dotted = proof.dotted
  lastProof = proof
  rounds.push({ round, exit_code: proof.exit_code, targets: proof.targets, counterexamples: proof.counterexamples.length })
  if (isProven(proof)) {
    proven = true
    const verdicts = proof.verdicts || []
    const confirmed = verdicts.filter((v) => /confirmed over all paths/i.test(v.verdict)).length
    const aside = setAside(proof)
    log(
      `round ${round}: ${module} has no counterexample within its declared bound; ${confirmed} of ${verdicts.length} condition(s) confirmed over all paths` +
        (aside.length ? `; ${aside.length} function(s) set aside under [tool.beethoven-qa.unproven] and not proven: ${aside.map((s) => `${s.name} (${s.reason})`).join('; ')}` : ''),
    )
    break
  }
  if (proof.counterexamples.length === 0) {
    stopped =
      proof.targets <= 0
        ? `the gate checked no contracted function or class in ${module}, so nothing was proven`
        : `the gate failed on ${module} but named no counterexample; its output is returned for a person`
    log(`round ${round}: ${stopped}`)
    break
  }
  const key = counterexampleKey(proof.counterexamples)
  if (key === lastKey) {
    stopped = 'the same counterexamples came back after a repair'
    log(`round ${round}: ${stopped}; stopping`)
    break
  }
  lastKey = key
  // A repair nothing proves afterwards would be reported as unproven on stale counterexamples.
  if (round === maxRounds) break
  phase('Repair')
  const repair = await agent(
    `The gate found these in ${module}:\n` +
      proof.counterexamples.map((c) => `- ${c.function}: input ${c.input} violates ${c.violated}`).join('\n') +
      `\n\nGate output:\n${proof.gate_output}\n\n` +
      `For each one decide whether the code or the contract is wrong, and fix that one thing minimally in ` +
      `${module} (or its test). Before deciding, call the function with the input in a test and watch it ` +
      'fail. A report that does not reproduce concretely is a fault of the tool: record the input, the line and ' +
      'the CrossHair version in your note, and never change the code, loosen a contract or lower the bound for ' +
      'it; only a precondition that states the callers\' real domain may exclude its input. An entry that violates "no contracts" is a public function the gate found ' +
      'without contracts: give it contracts that state its intent; if it reads files, imports, runs a process ' +
      'or touches the network, CrossHair must not run it, so say so in your note and leave it for a person ' +
      '(making it private changes what its callers import; /beethoven-qa:improve-module is the way to split ' +
      'it). An entry "Unable to meet precondition" means CrossHair found no input that meets the function\'s ' +
      'preconditions within the bound: if they contradict each other, the specification is wrong, so correct ' +
      'them to what the callers really pass; if they are only beyond the solver, state the same domain in a ' +
      'form it can meet; never by dropping a guarantee the body relies on. Keep the icontract and beartype ' +
      'layering the module has (@beartype outermost, the contracts from beethoven_qa.contracts beneath it).\n\n' +
      `${NEVER}\n\n` +
      'Do not commit. Report what changed and why, per counterexample, and every precondition you added or ' +
      'strengthened with the callers you checked.',
    { label: `repair ${module} round ${round}`, phase: 'Repair', schema: REPAIR },
  )
  if (!repair) {
    stopped = `the repair agent returned nothing in round ${round}; what it changed has not been proven`
    log(`${stopped}; stopping`)
    break
  }
  const preconditions = repair.preconditions || []
  repairs.push({ round, changed: repair.changed, files: repair.files, note: repair.note, preconditions })
  log(`round ${round}: ${repair.changed ? `${repair.files.length} file(s) changed` : 'nothing changed'}: ${repair.note}`)
  if (preconditions.length) {
    log(`round ${round}: ${preconditions.length} precondition(s) added or strengthened; a person checks each against its callers: ${preconditions.map((p) => `${p.function}: ${p.precondition}`).join('; ')}`)
  }
  // Proving unchanged code again would only repeat this round.
  if (!repair.changed) {
    stopped = `the repair in round ${round} changed nothing`
    log(`${stopped}; stopping`)
    break
  }
}

if (!proven) {
  if (!stopped) stopped = `not proven within ${maxRounds} round(s)`
  log(`${module}: not proven (${stopped}); the counterexamples are returned for a person`)
  return {
    module,
    proven: false,
    rounds,
    stopped,
    counterexamples: lastProof ? lastProof.counterexamples : [],
    set_aside: lastProof ? setAside(lastProof) : [],
    gate_output: lastProof ? lastProof.gate_output : null,
    repairs,
  }
}

// Humans review the contracts, not raw lines: one reading of the module's public functions, in words.
// A function the gate set aside has no verdict at all, and the reviewer must not be handed "no
// counterexample" for it.
phase('Review')
const aside = setAside(lastProof)
const review = await agent(
  `Read ${module}. For every public function, write its @require preconditions and @ensure ` +
    `postconditions (and class invariants) in plain words a reviewer can check against the intent, one ` +
    `line each, with each condition's verdict from the gate below ("confirmed over all paths", or "no ` +
    `counterexample within N s", N the declared bound), and name the declared symbolic bound ` +
    `(per_condition_timeout under [tool.beethoven-qa] in pyproject.toml). Change no file.\n` +
    `The gate's verdicts:\n${JSON.stringify(lastProof.verdicts || [], null, 1)}\n` +
    `Functions set aside under [tool.beethoven-qa.unproven], which the gate did not check as targets: mark each of ` +
    `their conditions "not proven: contracts checked at run time only (<reason>)", never "no ` +
    `counterexample":\n${JSON.stringify(aside, null, 1)}`,
  { label: `contracts of ${module}`, phase: 'Review', schema: REVIEW },
)

return { module, proven: true, rounds, verdicts: lastProof.verdicts || [], set_aside: aside, contracts: review ? review.functions : null, repairs }
