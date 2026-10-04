export const meta = {
  name: 'verify-module',
  description: 'The SpecPylot loop for one module: run the CrossHair/Z3 gate, feed each counterexample to a repair agent, repeat until proven or stuck, then write the contracts out for a human',
  phases: [
    { title: 'Prove', detail: 'pixi run symbolic, read for the named module' },
    { title: 'Repair', detail: 'one repair per counterexample round' },
    { title: 'Review', detail: 'the contracts in plain words, for the human reviewer' },
  ],
}
// Saved by the skeleton thread on 2026-10-04: step 6 of the definition of done (CLAUDE.md), "generate
// code + contracts → compile → CrossHair → feed a Z3 counterexample back → iterate until proven; humans
// review the contracts, not raw lines", as a script. Repairs are sequential (one editor in this
// checkout). A repair may change the code or the contract that is wrong; it never loosens a bound,
// removes a contract or exempts the module to make the gate pass.
//
// args: { module: 'src/beethoven_research/<name>.py', maxRounds?: number }
//       maxRounds defaults to 3; 0 runs no agent and is the syntax check of this file.

const PROOF = {
  type: 'object',
  required: ['proven', 'counterexamples', 'gate_output'],
  properties: {
    proven: { type: 'boolean', description: 'true only if `pixi run symbolic` exited 0 and reported no counterexample in the module' },
    counterexamples: {
      type: 'array',
      items: {
        type: 'object',
        required: ['function', 'input', 'violated'],
        properties: {
          function: { type: 'string', description: 'qualified name of the function' },
          input: { type: 'string', description: "CrossHair's counterexample input, verbatim" },
          violated: { type: 'string', description: 'the postcondition or invariant it violates, verbatim' },
        },
      },
    },
    gate_output: { type: 'string', description: 'the decisive lines of the gate output for this module' },
  },
}

const REPAIR = {
  type: 'object',
  required: ['changed', 'files', 'note'],
  properties: {
    changed: { type: 'boolean' },
    files: { type: 'array', items: { type: 'string' } },
    note: { type: 'string', description: 'for each counterexample: was the code wrong or the contract, and what changed' },
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
          ensures: { type: 'array', items: { type: 'string' }, description: 'each postcondition in plain words' },
          bound: { type: 'string', description: 'the declared symbolic bound, if any' },
        },
      },
    },
  },
}

const module = args && typeof args.module === 'string' ? args.module : 'src/beethoven_research/example.py'
const maxRounds = args && typeof args.maxRounds === 'number' ? args.maxRounds : 3
const rounds = []
let lastKey = null
let stuck = 0
let proven = false

for (let round = 1; round <= maxRounds; round++) {
  const proof = await agent(
    `Run \`pixi run symbolic\` in this checkout (the CrossHair/Z3 gate over the package) and read its ` +
      `output for the module ${module} only. Report proven=true only if the command exited 0 and no ` +
      `counterexample names a function of that module. List every counterexample for that module with ` +
      `the function, the input and the violated condition, verbatim. Change no file.`,
    { label: `prove ${module} round ${round}`, phase: 'Prove', schema: PROOF },
  )
  if (!proof) {
    log(`round ${round}: the prove agent returned nothing; stopping`)
    break
  }
  rounds.push({ round, proven: proof.proven, counterexamples: proof.counterexamples.length })
  if (proof.proven) {
    proven = true
    log(`round ${round}: ${module} proven within its declared bounds`)
    break
  }
  const key = JSON.stringify(proof.counterexamples)
  stuck = key === lastKey ? stuck + 1 : 0
  lastKey = key
  if (stuck >= 2) {
    log(`round ${round}: the same counterexamples two rounds running; stopping`)
    break
  }
  const repair = await agent(
    `CrossHair found inputs that satisfy a precondition of ${module} and violate a postcondition:\n` +
      proof.counterexamples.map((c) => `- ${c.function}: input ${c.input} violates ${c.violated}`).join('\n') +
      `\n\nGate output:\n${proof.gate_output}\n\n` +
      `For each one decide whether the code or the contract is wrong, and fix that one thing minimally in ` +
      `${module} (or its test). Never loosen a bound, remove a contract, widen a precondition to dodge the ` +
      `input, or exempt the module from the gate. Keep the icontract and beartype layering the module has. ` +
      `Do not commit. Report what changed and why, per counterexample.`,
    { label: `repair ${module} round ${round}`, phase: 'Repair', schema: REPAIR },
  )
  if (!repair) {
    log(`round ${round}: the repair agent returned nothing; stopping`)
    break
  }
  log(`round ${round}: ${repair.changed ? `${repair.files.length} file(s) changed` : 'nothing changed'}: ${repair.note}`)
  if (!repair.changed) stuck += 1
}

if (!proven) {
  log(`${module}: not proven after ${rounds.length} round(s); the counterexamples are returned for a person`)
  return { module, proven: false, rounds, counterexamples: lastKey ? JSON.parse(lastKey) : [] }
}

// Humans review the contracts, not raw lines: one reading of the module's public functions, in words.
const review = await agent(
  `Read ${module}. For every public function, write its @require preconditions and @ensure ` +
    `postconditions (and class invariants) in plain words a reviewer can check against the intent, one ` +
    `line each, and name the declared symbolic bound if the module declares one. Change no file.`,
  { label: `contracts of ${module}`, phase: 'Review', schema: REVIEW },
)

return { module, proven: true, rounds, contracts: review ? review.functions : null }
