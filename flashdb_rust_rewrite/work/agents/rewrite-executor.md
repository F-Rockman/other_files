# Agent Runbook: rewrite-executor

## Mission

Produce a complete Rust rewrite of FlashDB at `flashDB_rust/`, using the
original C source and tests as read-only reference material.

## Operating Rules

- The root `INSTRUCTION.md` is the entry point.
- `work/skills/flashdb-rust-rewrite/SKILL.md` is the migration method.
- `work/scripts/flashdb_pipeline.py` is the verification and reporting CLI.
- `work/agents/self-healer.md` repairs a stalled current task without restarting
  migration.
- `result/` stores self-validation records; `result/output.md` is success-only.
- `logs/process.jsonl` stores concise process events and `log/trace/` stores
  command output. Do not create `logs/interaction/` without real human
  interaction.
- Do not edit `src/`, `inc/`, or `tests/`.
- Do not wait for a human choice. Make conservative choices that follow the C
  behavior and the existing Rust layout.
- Prefer small edits with immediate `cargo check`.
- Treat exit code `75` as `CONTINUE_REQUIRED`, not as completion or a fatal
  failure. Read `work/state/continue.json` and execute its `next_action`.
- Never return a final answer while `work/state/continue.json` has
  `required: true`.

## Standard Cycle

1. Run:

   ```bash
   python3 work/scripts/flashdb_pipeline.py preflight
   python3 work/scripts/flashdb_pipeline.py plan
   python3 work/scripts/flashdb_pipeline.py advance
   ```

2. Open only `work/state/current_task.md`.

3. If the compact packet says `START_TASK`, run:

   ```bash
   python3 work/scripts/flashdb_pipeline.py advance TASK_ID
   ```

   This records only execution state and target-file hashes.

4. Read only the source ranges listed in the current task. Do not exceed the
   task's maximum read-line budget. Continue only when the task says
   `Read budget: OK`.

5. Edit only the target file(s) listed in the current task.

6. Run one lifecycle command immediately after the edit:

   ```bash
   python3 work/scripts/flashdb_pipeline.py advance TASK_ID
   ```

   It runs the check, records completion, and starts the next task when those
   transitions are valid. If it prints `SELF-HEAL APPLIED`, stop the
   old tactic, open the rewritten `work/state/current_task.md`, and perform its
   `Next required action` immediately.

   Tasks above 120 source lines with multiple completion symbols are focused
   proactively. A focus may contain two adjacent functions only when the
   merged source range is at most 80 lines.

   Calling `advance` again without editing invokes in-place self-healing and
   skips Cargo. Follow the resulting focus instead of repeating the command.

7. Repeat until:

   ```bash
   python3 work/scripts/flashdb_pipeline.py verify --strict
   ```

   exits 0.

## Blocked Execution

Run the self-healer as soon as any of these is true:

- context was compacted after reading source but before editing;
- the same source range is about to be read again without a target change;
- the check stage reports the same compiler error twice;
- Cargo succeeds but the focused completion symbol remains missing;
- the current task exceeds its read budget.

Run:

```bash
python3 work/scripts/flashdb_pipeline.py heal TASK_ID
```

Then continue from the rewritten current task. Do not rerun `preflight`, restart
at T00, create an attempt archive, or discard valid Rust source.

If `plan` reports a read-budget violation, stop execution, split the named
task with `heal`, and continue only after the active focus is budget-safe.

## Context Compression Recovery

After any context compaction or model restart:

1. Run `python3 work/scripts/flashdb_pipeline.py advance`.
2. Read only `work/state/continue.json`, `work/state/healing_action.md`, and
   `work/state/current_task.md`.
3. Perform `next_action` immediately. Exit `75` means keep going.
4. Continue the same parent task.

Do not reread `README`, full C files, previous trace files, or the whole
`work/` directory. The task file is the recovery point.
Do not create task notes containing source understanding or implementation
hints; only non-semantic execution state is allowed.

## Hard Stop Conditions

Stop the current tactic and invoke `heal` if any of these occur:

- The model wants to read more than the task's line budget.
- A task requires more than 200 estimated source lines.
- The model tries to implement more than one task at once.
- The model wants to store source understanding in task notes.
- `plan` reports a read-budget violation.
- The same cargo error appears twice.
- Context compaction happens once without a successful edit/check cycle.

## Cleanup Policy

Self-heal may replace `current_task.md`, `healing_action.md`, the active task's
progress record, and redundant task-check traces under `log/trace/`. It must not
delete original C material, completed-task records, or valid Rust source.
Replace a blocking stub with implementation rather than deleting the project.
