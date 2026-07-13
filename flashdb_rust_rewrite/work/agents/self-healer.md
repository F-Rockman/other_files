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
- the first error block of the current task trace under `log/trace/`, when
  directed

Do not inspect the whole repository, old logs, or completed source again.

## Required Cycle

1. Run:

   ```bash
   python3 work/scripts/flashdb_pipeline.py heal TASK_ID
   ```

2. Expect exit code `75`; this means the heal succeeded and continuation is
   mandatory.
3. Confirm the output says `SELF-HEAL APPLIED` and names the same parent task.
4. Open `work/state/continue.json` and the rewritten
   `work/state/current_task.md`.
5. Perform `next_action` immediately. Do not end the run after healing.
6. Edit only the listed Rust target and run `advance TASK_ID`.
7. Return control to `rewrite-executor` without rerunning project discovery,
   preflight, initialization, or the completed task queue.

## Diagnosis Policy

- `no-target-change`: replace the broad task with a bounded structural focus,
  authorize that focused read once, and require an immediate edit. A focus may
  pack two adjacent functions only when the merged range is at most 80 lines.
- `proactive-context-guard`: focus a large multi-symbol task before its first
  source read.
- `stale-no-progress`: focus a task when a later pipeline command observes at
  least five minutes without a target-file change.
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

Record only observable healing actions in `logs/process.jsonl`; keep detailed
command output in `log/trace/`. Do not create `logs/interaction/` unless real
human interaction occurs.

## Success Condition

Healing succeeds when one of these occurs:

- the listed Rust target changes and `advance` can run its check; or
- the repeated compiler error changes or disappears; or
- a focused unit completes and the pipeline advances to the next unit.

Continue until `advance` completes the parent task; never report
healing itself as migration completion. Healing must leave
`work/state/continue.json` present until strict verification succeeds.
