# FlashDB C-to-Rust Rewrite - Autonomous Execution Guide

> This is the authoritative entry point for the evaluator LLM.
> The evaluator's repository path is expected to be
> `/app/code/judge-assets/02_02_c_to_rust/code/FlashDB`.
> All commands below also work from any other checkout when run from the
> repository root. Do not hardcode a local developer path.

## Objective

Rewrite the original FlashDB C implementation under `src/` into an idiomatic,
buildable Rust project under `flashDB_rust/`, and migrate or equivalently cover
the original tests under `tests/`.

The final deliverable must contain:

- `flashDB_rust/Cargo.toml`
- `flashDB_rust/src/`
- `flashDB_rust/tests/`
- `result/output.md`
- `result/issues/00-summary.md`
- `result/verify.json`
- `logs/process.jsonl`
- `log/trace/`

The Rust project must pass `cargo build` and `cargo test`, and the ratio of
`unsafe` source lines must be below 10%.

## Environment Preparation

Run from the repository root:

```bash
cd /app/code/judge-assets/02_02_c_to_rust/code/FlashDB
python3 work/scripts/flashdb_pipeline.py preflight
```

Required tools:

- `python3`
- `rustc` and `cargo` from a stable Rust toolchain, Rust 1.70 or newer
- a POSIX shell
- `git`, when available, for checking that original C materials were not
  modified

No network access, human interaction, service startup, MCP server, Docker
daemon, or external package installation is required by the workflow. The Rust
project may use only:

- `crc32fast`
- `bytemuck`
- `tempfile` as a dev-dependency

If `flashDB_rust/` is missing, create the minimal project skeleton without
overwriting existing work:

```bash
python3 work/scripts/flashdb_pipeline.py init
```

Before migration, read `work/agents/rewrite-executor.md`. When the pipeline
prints `SELF-HEAL APPLIED`, follow `work/agents/self-healer.md` and continue the
same current task immediately.

## Execution Method

This is a low-context micro-task workflow. It is designed for evaluator LLMs
that cannot keep the full project in context.

Do not read entire C source files. Do not attempt to implement `kvdb.rs` or
`tsdb.rs` in one pass. Always let the pipeline select the next small task.

1. Generate the micro-task queue:

   ```bash
   python3 work/scripts/flashdb_pipeline.py plan
   python3 work/scripts/flashdb_pipeline.py task
   ```

   The current task is also written to:

   ```text
   work/state/current_task.md
   work/state/todo.md
   ```

   If `plan` exits non-zero with a read-budget violation, stop immediately and
   split the named task in `work/scripts/flashdb_pipeline.py`. Do not ask the
   model to continue with an over-budget task.

2. For the current task, read only the listed source ranges. Each task states a
   maximum read-line budget and an estimated read-line count. Proceed only when
   `Read budget: OK` is shown. If you reach the budget, stop reading and write.
   Before reading source, mark the task as started:

   ```bash
   python3 work/scripts/flashdb_pipeline.py start-task TASK_ID
   ```

   `start-task` records only execution state and target-file hashes. It must
   not store source-code understanding, algorithm summaries, or implementation
   hints.

3. Edit only the task's listed target file(s).

4. Run the task's check wrapper, then:

   ```bash
   python3 work/scripts/flashdb_pipeline.py check-task TASK_ID
   python3 work/scripts/flashdb_pipeline.py complete-task TASK_ID
   python3 work/scripts/flashdb_pipeline.py task
   ```

   `check-task` also checks whether the target changed. If no edit was made, it
   skips a redundant Cargo invocation, applies a smaller active focus, and
   prints `SELF-HEAL APPLIED`.

5. Repeat the micro-task loop until `task` points at `T33-final-verify`.

6. If execution stalls, context is compacted before an edit, or the same check
   error repeats, heal the current task in place:

   ```bash
   python3 work/scripts/flashdb_pipeline.py heal TASK_ID
   ```

   `heal` diagnoses observable execution state, replaces the active task with
   smaller symbol-focused work or first-error repair, rewrites
   `work/state/current_task.md`, and continues the same parent task. It must not
   restart the queue, discard completed work, or archive `flashDB_rust/`.

   Repeating `start-task` before a target edit automatically invokes this same
   healing path. `refresh --reason "..."` remains only as a compatibility alias
   for `heal`; it does not start another attempt.

7. Run the final verifier:

   ```bash
   python3 work/scripts/flashdb_pipeline.py verify --strict
   ```

### Context Budget Rules

- Read at most one `work/state/current_task.md` plus the exact source ranges
  listed in it.
- No micro-task should require more than 200 estimated source lines. If it
  does, split it before executing.
- Never read `src/fdb_kvdb.c` or `src/fdb_tsdb.c` from top to bottom.
- Never read generated logs larger than the first error block; inspect only the
  first failing error under `log/trace/`.
- After two read operations, the next action must be an edit or a check command.
- If context is compacted before an edit, run
  `python3 work/scripts/flashdb_pipeline.py heal TASK_ID`. Then read only the
  newly focused range in `work/state/current_task.md` and edit immediately.
- Obey `Next required action` in `work/state/current_task.md`; do not improvise
  another discovery or planning loop.
- Do not create or rely on task notes that contain FlashDB source
  understanding. Only non-semantic execution state may be stored under
  `work/state/`.
- `work/state/healing_action.md` may contain task ids, structural source ranges,
  target hashes, compiler-log locations, and counters only. It must not contain
  algorithm summaries or implementation hints.

## Completion Decision

The work is complete only when this command exits with status 0:

```bash
python3 work/scripts/flashdb_pipeline.py verify --strict
```

Strict verification checks all of the following:

- root `INSTRUCTION.md` exists
- `flashDB_rust/Cargo.toml` exists
- `flashDB_rust/src/*.rs` exists
- `flashDB_rust/tests/*.rs` exists
- `cargo build` exits 0
- `cargo test` exits 0 and runs at least 24 tests
- all mapped FlashDB test cases are present
- `unsafe` ratio is below 10%
- `result/output.md` is generated and non-empty
- `result/issues/00-summary.md` is generated
- `result/verify.json` contains the final self-validation record
- `logs/process.jsonl` contains concise execution records
- `log/trace/` contains command and verification traces
- original material under `src/`, `inc/`, and `tests/` has not been modified

Any failure means the rewrite is not complete. Use `work/state/next_actions.md`
as the next repair checklist.

## Result Retrieval

After strict verification succeeds, the evaluator can find the final
deliverables at:

```text
flashDB_rust/
flashDB_rust/Cargo.toml
flashDB_rust/src/
flashDB_rust/tests/
result/output.md
result/issues/00-summary.md
result/preflight.json
result/status.json
result/verify.json
logs/process.jsonl
log/trace/
```

### Record Directory Semantics

- `result/` stores self-validation records. `preflight.json`, `status.json`,
  `verify.json`, and `issues/00-summary.md` may exist before completion.
- `result/output.md` stores successful-run output only. Do not create it, and
  remove any stale copy, while strict verification is failing.
- `logs/` stores concise process and decision records. Record observable
  actions and outcomes, not hidden chain-of-thought or source-understanding
  notes.
- `logs/interaction/` stores contestant/work human-interaction records. This
  workflow requires no human interaction, so the directory must remain absent
  unless an interaction actually occurs.
- `log/trace/` stores detailed command, compiler, test, and verification traces.

`result/output.md` must summarize:

- final Rust project location
- build result
- test result
- test migration/coverage status
- unsafe ratio
- original-source integrity status

`result/issues/00-summary.md` must summarize:

- unresolved issues, if any
- deviations from the C behavior, if any
- blocked steps and repair actions, if verification has not yet passed

## Absolute Prohibitions

- Do not omit this root `INSTRUCTION.md`.
- Do not require manual interaction.
- Do not depend on the current local path outside the repository.
- Do not modify the evaluator-provided original materials under `src/`, `inc/`,
  or `tests/`.
- Do not produce only compiled artifacts; reproducible Rust source and build
  steps are required.
- Do not leave `flashDB_rust/Cargo.toml` missing.
- Do not leave the Rust project unbuildable.
- Do not skip or rename original C test semantics without equivalent coverage.
- Do not use `unsafe` as a shortcut; keep it below 10% and justify every block.
