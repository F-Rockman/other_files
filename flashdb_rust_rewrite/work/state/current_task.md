# Current Micro Task: T33-final-verify

Title: Run strict verification and finalize reports
Parent done: True
Estimated read lines: 120
Max read lines before writing/checking: 120
Read budget: OK
Task started: NO
Target changed since start: NO
Recorded failures: 0
Source reread blocked: NO
Self-heal active: NO
Self-heal generation: 0
Continuation required: NO
Continuation exit code: 75
Next required action: RUN_FINAL_VERIFY

Read only these focused ranges:
- work/state/next_actions.md:1-120

Write/edit only these targets:
- result/output.md
- result/issues/00-summary.md

Check command:
`python3 work/scripts/flashdb_pipeline.py verify --strict`

Preferred check wrapper:
`python3 work/scripts/flashdb_pipeline.py advance T33-final-verify`

Protocol:
1. Obey `Next required action`; use `start-task T33-final-verify` only when it says START_TASK.
2. When a focused read is authorized, read it once and edit before any other discovery.
3. Do not write source-understanding notes; only execution state may persist.
4. Run `advance` immediately after the edit; it checks, completes, and starts the next task when possible.
5. If progress is impossible or context was compacted before an edit, run `python3 work/scripts/flashdb_pipeline.py heal T33-final-verify`.
6. Exit code 75 means continue immediately; it is neither completion nor a fatal error.
7. Self-heal changes the active focus and continues this parent task; it never restarts migration.
