---
name: flashdb-rust-rewrite
description: Rewrite FlashDB C sources and tests into a buildable Rust project through bounded micro-tasks, deterministic checks, and in-place self-healing. Use for evaluator-driven FlashDB C-to-Rust migration, low-context execution, migration stalls, repeated compiler failures, and final delivery verification.
---

# FlashDB Rust Rewrite Orchestrator

## Purpose

Guide an evaluator LLM to rewrite FlashDB from C to Rust as a reproducible
deliverable. The skill favors an architecture-first, test-preserving rewrite
over blind line-by-line translation.

## Industry-Informed Approach

Use these principles throughout the rewrite:

1. Treat mechanical C-to-Rust translation as a scaffold only. The final code
   should be idiomatic Rust with explicit ownership and safe abstractions.
2. Preserve behavior with characterization tests. Port the original C tests
   into Rust integration tests before declaring completion.
3. Keep unsafe code at the boundary. Prefer slices, `std::fs`, enums,
   `Result`, and typed structs over raw pointers and C inheritance casts.
4. Use Cargo's standard package layout: `Cargo.toml`, `src/`, and `tests/`.
5. Work in small, compiling increments. Let `advance` select the narrow Cargo
   check; run full tests at behavior boundaries and during final verification.
6. Heal process artifacts in place when they cause stalls. Preserve completed
   tasks and valid Rust source, optimize the active task, and continue it.
7. Prefer a simple, composable prompt-chaining workflow with programmatic gates
   over a broad autonomous agent. Each task must have a narrow input, a clear
   output file, a check command, and a stop condition.
8. Treat `work/state/current_task.md` as the checkpoint boundary. It is the only
   state a compacted or restarted model needs to resume.

## Authoritative Paths

Run all commands from the repository root.

```text
Original C source:     src/
Original C headers:    inc/
Original C tests:      tests/
Rust output:           flashDB_rust/
Self-validation:       result/preflight.json
                       result/status.json
                       result/verify.json
Successful output:     result/output.md
                       result/issues/00-summary.md
Process records:       logs/process.jsonl
Human interaction:     logs/interaction/ (only when interaction occurs)
Execution traces:      log/trace/
Pipeline CLI:          work/scripts/flashdb_pipeline.py
Executor runbook:      work/agents/rewrite-executor.md
Self-healer runbook:   work/agents/self-healer.md
```

## Do Not Modify

These directories are read-only reference material:

```text
src/
inc/
tests/
```

## Required Rust Layout

```text
flashDB_rust/
  Cargo.toml
  src/
    lib.rs
    error.rs
    def.rs
    blob.rs
    low_lvl.rs
    utils.rs
    file_backend.rs
    db.rs
    kvdb.rs
    tsdb.rs
  tests/
    kvdb_test.rs
    tsdb_test.rs
```

## Module Mapping

| C material | Rust target | Role |
|---|---|---|
| `src/fdb_utils.c` | `src/utils.rs` | CRC32 and byte/string helpers |
| `src/fdb_file.c` | `src/file_backend.rs` | POSIX file-mode flash emulation |
| `src/fdb.c` | `src/db.rs` | base DB initialization and control |
| `src/fdb_kvdb.c` | `src/kvdb.rs` | KVDB storage, GC, recovery, public API |
| `src/fdb_tsdb.c` | `src/tsdb.rs` | TSDB append, iteration, query, cleanup |
| `inc/fdb_def.h` | `src/def.rs`, `src/error.rs`, `src/blob.rs` | constants, enums, blob, structs |
| `inc/fdb_low_lvl.h` | `src/low_lvl.rs` | alignment and status table helpers |
| `tests/fdb_kvdb_tc.c` | `tests/kvdb_test.rs` | 13 KVDB behavior tests |
| `tests/fdb_tsdb_tc.c` | `tests/tsdb_test.rs` | 11 TSDB behavior tests |

## Execution Loop

Use the pipeline micro-task queue. Do not invent a separate plan.

```bash
python3 work/scripts/flashdb_pipeline.py plan
python3 work/scripts/flashdb_pipeline.py advance
```

For the current task:

1. Run `python3 work/scripts/flashdb_pipeline.py advance TASK_ID`; it starts an
   unstarted task automatically.
2. Read only the ranges listed in `work/state/current_task.md`.
3. Stay under the task's `Max read lines before writing/checking` value.
   Continue only when the task prints `Read budget: OK`.
4. Edit only the listed target files.
5. Run `python3 work/scripts/flashdb_pipeline.py advance TASK_ID`.
6. Let that command check the edit, complete the task, and start the next task
   whenever its gates pass.

Treat exit code `75` from task lifecycle commands as `CONTINUE_REQUIRED`. Read
`work/state/continue.json`, perform `next_action`, and do not return a final
answer. Only strict verification exiting `0` is completion.

When a lifecycle command prints `SELF-HEAL APPLIED`, open the rewritten
`work/state/current_task.md` and execute `Next required action` immediately.

The read/write rhythm matters. After at most two read operations, perform an
edit or run a check. Re-reading the same source without code output is a stall.
If `plan` reports a read-budget violation, split that task first and do not
continue execution.

## Low-Context Safety Rules

- Never load all of `src/fdb_kvdb.c`, `src/fdb_tsdb.c`, or either C test file.
- Keep every micro-task at or below 200 estimated source lines. Split the task
  before execution if it exceeds that limit.
- Proactively focus tasks above 120 source lines when they contain multiple
  completion symbols. Do not wait for the first context stall.
- Pack at most two adjacent functions into one focus only when their merged
  source range is at most 80 lines.
- Use compact lifecycle output by default. `task --full` is diagnostic only;
  the full checkpoint remains in `work/state/current_task.md`.
- Rely on the verified completion cache and frontier scan during execution;
  do not regenerate or rescan the entire queue after each edit.
- Repeating `advance` without a target edit invokes in-place self-healing and
  never launches a redundant Cargo command.
- Never ask the model to "summarize the whole project" during execution.
- Do not keep full C source snippets in the answer. Convert the current snippet
  into Rust, run the check, and discard it.
- Do not write task notes containing FlashDB source understanding, algorithm
  summaries, or implementation hints. The only allowed persisted checkpoint is
  non-semantic execution state: task id, target-file hashes, check exit code,
  and failure count.
- If context compaction occurs, resume with:

  ```bash
  python3 work/scripts/flashdb_pipeline.py heal TASK_ID
  ```

  Do not rerun broad discovery. Read only the new focused range, edit its target
  immediately, and continue the same parent task.
- If the same check error occurs twice, the check stage invokes first-error repair.
  Follow it instead of entering another compression cycle.

## Record Policy

- Store self-validation records under `result/`.
- Write `result/output.md` only after successful strict verification; remove a
  stale copy after any failed verification.
- Store concise observable process records under `logs/`. Do not write hidden
  chain-of-thought, source summaries, or implementation hints.
- Create `logs/interaction/` only when contestant/work human interaction
  actually occurs. Leave it absent for this autonomous workflow.
- Store command, compiler, test, and verification traces under `log/trace/`.

## In-Place Self-Healing

Use `heal` to diagnose observable process state and optimize the active task:

```bash
python3 work/scripts/flashdb_pipeline.py heal TASK_ID
```

The healing layer may:

- narrow a source range to one mechanically located C function;
- require one Rust completion symbol at a time;
- switch from source reading to the first compiler error block;
- replace stale current-task instructions and active progress state;
- remove obsolete transient healing notes.

Healing writes `work/state/continue.json` and returns exit `75`. This is a
successful handoff that requires immediate execution of the focused action, not
a reason to stop. `task` and `status` also heal a started unchanged task after
five minutes when they next run.

It must preserve `flashDB_rust/`, completed tasks, and the parent task id. It
must not start another attempt, return to T00, or save source-code understanding.
Continue with `work/state/current_task.md` as soon as healing returns.

## KVDB Implementation Layers

These are implemented through tasks `T07` through `T19`, including `T18a` and
`T18b`. Do not combine them. The task queue separates KVDB layout, cache,
read/scan, sector metadata, allocation, GC, public set/delete APIs,
print/auto-update, load/recovery, init/control/deinit, iterator, and integrity
check work.

## TSDB Implementation Layers

These are implemented through tasks `T20` through `T27`. The queue separates
layout, readers, sector formatting, append, forward/reverse iteration,
time-range query, status/clean/control, and init/deinit work.

## Test Migration Layers

KVDB tests are implemented through tasks `T28` through `T30`, with harness,
basic tests, iteration helpers, GC phases, scale-up, default reset, and deinit
split into separate micro-tasks.

TSDB tests are implemented through tasks `T31` through `T32`, with harness,
basic behavior, time-query helpers, boundary queries, deinit, and the GitHub
issue regression separated by task.

## Test Migration Requirements

KVDB tests must cover:

- initialization
- oldest address check
- blob create/change/delete
- string create/change/delete
- GC scenario 1
- GC scenario 2 with large KV
- scale-up from 4 to 8 sectors
- reset to default KV
- deinit

TSDB tests must cover:

- initialization
- clean
- append
- iterate all
- iterate by time
- query count
- set status
- clean after status changes/restart
- sector-boundary time query
- deinit
- GitHub issue 249 regression behavior

Use Rust's standard test framework. Integration tests under `flashDB_rust/tests`
are required.

## Verification Commands

Use the pipeline as the single source of truth:

```bash
python3 work/scripts/flashdb_pipeline.py preflight
python3 work/scripts/flashdb_pipeline.py plan
python3 work/scripts/flashdb_pipeline.py advance
python3 work/scripts/flashdb_pipeline.py status
python3 work/scripts/flashdb_pipeline.py verify
python3 work/scripts/flashdb_pipeline.py verify --strict
```

Completion requires `verify --strict` to exit 0.
The successful verifier also removes `work/state/continue.json`; otherwise the
workflow is still active.

## Compatibility Rule

`refresh` is retained only for older prompts:

```bash
python3 work/scripts/flashdb_pipeline.py refresh --reason "..."
```

It aliases in-place `heal`. It must not archive an attempt, restart migration,
or delete original C materials.
