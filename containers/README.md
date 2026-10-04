# The substrate image

`claude-verify.def` builds the one image every engine job runs in: Ubuntu 24.04, Node 22, the Claude
Code CLI, this repository's pixi environment reconstructed from `pixi.lock` with
`pixi install --locked`, and the policy that no job can loosen (`managed-settings.json` at Claude
Code's managed path, and the PreToolUse guard it names). The image is the *substrate*; the engine's
code is bound into it at run time at a pinned git SHA, so the image hash identifies the environment
and the Run ID identifies the code.

## Build

On the cluster, inside a Slurm job on a compute node (never the login node):

```sh
module load apptainer/1.2.2           # Wayne State; other sites differ
bash containers/build.sh --out /rs/rs_grp_oschome/shared/beethoven/containers
```

`build.sh` uses `--fakeroot`, keeps temp and cache on the node's local disk under `/tmp/$USER` with
a cleanup trap (never `$TMPDIR`, which on Wayne State is CephFS), and writes `claude-verify.digest`
beside the image: the sha256 of the `.sif`, the apptainer, Claude Code and pixi versions read back
from inside the image, the recipe's git SHA and the lock's sha256. **Commit the digest file** when an
image is published; the `.sif` itself is never committed.

The recipe's `%post` refuses to finish unless the managed settings parse and the guard, run exactly
as the settings name it, answers exit 2 to `rm -rf /`: a guard that cannot start would let every
call through as a non-blocking hook error, which is the one failure the image must not ship.

**Status:** the recipe builds in CI (lane `image (apptainer build)`; first run 2026-10-04: apptainer
1.3.6 from Ubuntu's archive, about two minutes, Claude Code 2.1.289 landed, image sha256
`b8c07c3adcc9f2f53fd7ab5d8d2289b8bd2407b0e3b0fca88a458154c57408a1`, a runner's image that is
discarded with the job). **No grid build yet and no committed digest**: the first build on a compute
node, with its digest committed beside the recipe, is Linear BEE-103's acceptance.

## Run

Always the three flags, so a job sees nothing of the host it was not given on purpose:

```sh
apptainer exec --containall --cleanenv --no-home \
    --bind "$CHECKOUT":/work \
    --bind "$RUN_DIR":/run_dir \
    --bind "$HOME/.beethoven/accounts/$ACCOUNT/claude":/claude \
    --bind /tmp/$USER/hpcworker_$SLURM_JOB_ID:/tmp \
    --env CLAUDE_CONFIG_DIR=/claude \
    --env BEETHOVEN_RUN_ROOT=/run_dir \
    claude-verify.sif pixi run --manifest-path /opt/beethoven-research/pixi.toml gate
```

- `--containall --cleanenv --no-home`: no host environment variables, no home directory, no stray
  mounts. The recipe's `%environment` additionally unsets every credential variable it knows of.
- One of the student's own Claude sign-in directories, the account the orchestrator chose for this
  job (`$ACCOUNT` is its label and the path is `accounts.json`'s; `docs/ACCOUNTS.md`, D4), is bound **read-write** and named by
  `CLAUDE_CONFIG_DIR`, because Claude Code writes its state there: the session transcripts,
  `.claude.json`, and the credential it refreshes itself (the launcher's worker binds it the same
  way). What keeps the engine out of it is not a mount flag but three lines of policy: the managed
  deny rules (`Read(//claude/**)`, `Edit(//claude/**)`, `Read(**/.credentials.json)`), the guard,
  which refuses any command or path that names the directory, and the engine's own code, which
  starts Claude Code there and asks only `claude auth status` (CLAUDE.md, non-negotiables).
- Node-local scratch is bound at `/tmp` explicitly; nothing of the job's runtime state goes to the
  parallel filesystem while it runs (Goal 5). `BEETHOVEN_RUN_ROOT` names the step's run directory,
  the one place besides the project and `/tmp` where the guard lets the agent write or delete.

## Policy inside the image

`managed-settings.json` lands at `/etc/claude-code/managed-settings.json`, the top of Claude Code's
settings precedence (checked against code.claude.com/docs on 2026-10-04): nothing a project, user
or `--settings` file says loosens it. It carries only what must never be loosened:

| Locked in the image | Why |
| - | - |
| `permissions.disableBypassPermissionsMode: "disable"` | no step runs with permissions off |
| `permissions.deny`: recursive and forced deletion, privilege and system commands, daemons and mass kills, remote shells and transfers, scheduler administration and other people's jobs, history rewrites, `claude setup-token`, `claude auth logout`; `Read` of the sign-in directory, credential files, `.env`, keys; `Edit` of the sign-in directory, `/etc`, `/usr`, `/opt`, `/var`, `/boot` | the non-negotiables, as prefix rules; a `Read` deny also blocks `Edit` and `Write` on the path |
| `allowManagedHooksOnly: true` and the PreToolUse guard on `Bash` and `Edit\|Write\|NotebookEdit` | the guard sees the whole command and the resolved path, which prefix rules cannot; no project hook replaces it |
| `env`: `BASH_DEFAULT_TIMEOUT_MS` 10 min, `BASH_MAX_TIMEOUT_MS` 30 min, `MCP_TIMEOUT`, `MCP_TOOL_TIMEOUT`, `MAX_MCP_OUTPUT_TOKENS`, `CLAUDE_CODE_TOOL_MEMORY_LIMIT=16G`, `DISABLE_AUTOUPDATER`, `DISABLE_UPDATES`, `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_ARTIFACT`, `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1` | every step bounded; a read-only image never tries to update itself; nothing published to claude.ai from a job; no nested subagent trees |

What is **not** in the file, on purpose: anything a job tunes per step. An `env` value in managed
settings overwrites the same variable exported by the job, so the effort level
(`CLAUDE_CODE_EFFORT_LEVEL`), the subagent concurrency cap (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`,
set to the allocation's cores), the auto-compact window and threshold
(`CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`), a prompt-cache TTL if one is
ever chosen, and `BEETHOVEN_RUN_ROOT` are the job template's (Goal 5, Linear BEE-132), and so are
`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (a dynamic workflow's agent cap) and the step's
`--settings` value, which carries `permissions.allow: ["Workflow"]` (in `-p` the Workflow tool is a
tool call like any other, and `dontAsk` would deny it) and `crossSessionInbound: "accept"` (a `-p`
session otherwise holds a sibling's message for five minutes and drops it). `disableWorkflows` is
deliberately absent from the image. `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is set nowhere: in `-p`
no teammate is spawned whatever its value (`docs/PARALLEL.md`). The
launcher's worker sets `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` *off* because Remote Control needs
feature flags; the engine's steps do not use Remote Control, but the same four narrower switches are
used here rather than the blanket one, so the two stay comparable.

Claude Code's own bubblewrap sandbox is left unset: whether it can nest inside an unprivileged
Apptainer on the grid is not yet measured, and the container is the isolation layer.

## The guard

`src/beethoven_research/guard.py` is baked at `/opt/beethoven-research/hooks/guard.py` and run with
the image's own python. It reads the PreToolUse event on stdin and answers 0 (allowed), 2 (blocked,
reason on stderr, which the model reads) or a JSON `ask` for the two borderline cases
(`scancel -u`, `sbatch --wrap`), which a `dontAsk` batch step turns into a refusal. Any failure
inside the guard blocks the call. It is a contracted module like every other: beartype on the
calls, icontract pre- and postconditions, and the CrossHair/Z3 gate proves that a refusal always
rests on a listed trigger token or the sign-in directory's path (so it cannot block `pytest`), and
that a path inside the sign-in directory is never writable. `tests/test_guard.py` pins the rule
table, spelling by spelling.

Beethoven runs Claude Code and is not an Anthropic product; "Claude" and "Claude Code" are
Anthropic's marks, used here to say what it runs.
