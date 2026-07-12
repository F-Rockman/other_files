# Agent Runbook: self-healer

## Mission

Detect why the current FlashDB migration task stopped making progress, optimize
the active execution artifacts, and continue the same task. Self-healing is not
a new migration attempt.

## Inputs

Read only:

- `work/state/current_task.md`
- `work/state/healing_action.md`, when present
- the current task's JSON under `work/state/task_progress/`
- the first error block of the current task log, when directed

Do not inspect the whole repository, old logs, or completed source again.

## Required Cycle

1. Run:

   ```bash
   python3 work/scripts/flashdb_pipeline.py heal TASK_ID
   ```

2. Confirm the output says `SELF-HEAL APPLIED` and names the same parent task.
3. Open the rewritten `work/state/current_task.md`.
4. Perform its `Next required action` immediately.
5. Edit only the listed Rust target and run `check-task TASK_ID`.
6. Return control to `rewrite-executor` without rerunning project discovery,
   preflight, initialization, or the completed task queue.

## Diagnosis Policy

- `no-target-change`: replace the broad task with one symbol-focused C function
  range, authorize that focused read once, and require an immediate edit.
- `repeated-check-failure`: stop reading C, expose only the first compiler error
  block, repair it, and resume the active symbol focus after Cargo succeeds.
- `check-passed-objective-incomplete`: keep the parent task open and focus only
  the missing completion symbol.
- `read-budget-too-large`: split the active structural range before any source
  read.
- `untracked-stall`: initialize healing from the current incomplete task without
  deleting existing Rust code.

## Artifact Rules

Allowed persisted healing data is non-semantic execution state only: task and
focus ids, structural source ranges, required symbol names already present in
the task queue, target hashes, compiler-log paths, timestamps, and counters.

Do not persist C algorithm summaries, translated pseudocode, implementation
hints, or hidden source excerpts. Do not modify `src/`, `inc/`, or `tests/`.
Do not delete `flashDB_rust/` or completed-task state.

## Success Condition

Healing succeeds when one of these occurs:

- the listed Rust target changes and `check-task` can run; or
- the repeated compiler error changes or disappears; or
- a focused unit completes and the pipeline advances to the next unit.

Continue until the parent task can be passed to `complete-task`; never report
healing itself as migration completion.
