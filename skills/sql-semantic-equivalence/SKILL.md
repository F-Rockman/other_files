---
name: sql-semantic-equivalence
description: Compare two SQL queries for equivalence of core analytical intent and result semantics across dialects and syntactic rewrites, then explain material differences in Chinese. Use when users ask whether SQL queries mean the same thing; do not use for formatting-only diffs or performance-only tuning.
---

# SQL Semantic Equivalence

Determine whether two SQL queries pursue the same core purpose. Ignore cosmetic and
dialect-level differences only when their semantics are genuinely equivalent. Explain
every material mismatch in terms of its effect on the result or business meaning.

## Distinguish two questions

Always assess both layers rather than collapsing them into one vague similarity score:

1. **Core intent:** Do the queries ask for the same subject, population, grain,
   measures, dimensions, time frame, and essential relationships?
2. **Result semantics:** Under the stated schema, dialect, and assumptions, do they
   return the same row multiset and values? SQL results are bags unless a construct
   such as `DISTINCT` makes them sets.

Queries may share a topic while differing materially. For example, counting orders
and counting distinct customers are related, but their core analytical purposes are
not the same.

## Handle inputs

- Accept SQL embedded in prose, files, code fences, or separate dialects.
- Always write the comparison result in concise Simplified Chinese. Preserve SQL
  identifiers, literals, and quoted fragments verbatim.
- Use supplied schemas, key constraints, nullability, dialects, business definitions,
  and sample data. Never invent them.
- If missing context could change the verdict, proceed with explicit alternatives or
  assumptions when practical. Ask a question only when no useful conditional verdict
  can be given.
- For `INSERT`, `UPDATE`, `DELETE`, DDL, or procedural SQL, compare affected objects,
  row selection, written values, and side effects. Never execute mutating SQL merely
  to compare it.

## Comparison workflow

1. Classify each statement and infer its concise business-purpose summary.
2. Build a semantic fingerprint for each query:
   - output columns and their meanings;
   - output entity and grain;
   - source tables, subqueries, and lineage;
   - row eligibility and filter placement;
   - join types, keys, direction, and possible cardinality expansion;
   - grouping, aggregates, formulas, and distinctness;
   - set operations, duplicate behavior, and null behavior;
   - window partitions, ordering, frames, ranking, and tie behavior;
   - time boundaries, time zones, calendars, and snapshot semantics;
   - ordering, limiting, sampling, nondeterminism, and session-dependent behavior.
3. Normalize only semantics-preserving surface differences. Safe candidates include
   formatting, keyword case, harmless aliases, reordered pure conjuncts, equivalent
   quoting, and CTE-versus-derived-table rewrites. Treat dialect functions as
   equivalent only after checking types, nulls, precision, time zones, and boundary
   behavior.
4. Compare the fingerprints using
   [the semantic rubric](references/semantic-rubric.md). Record every assumption on
   which equivalence depends.
5. When a material difference is found, explain the causal chain:
   **construct difference -> affected rows/values -> business impact**.
6. When useful and feasible, construct a minimal counterexample using
   [the counterexample guide](references/counterexample-guide.md).

## Verdicts

Use Chinese display labels in the response. Internally distinguish all three fields:

- **总体结论:** `一致`, `有条件一致`, `不一致`, or `无法判断`.
- **核心目的:** `相同`, `相关但不同`, `不同`, or `无法判断`.
- **结果语义:** `等价`, `满足条件时等价`, `不等价`, or `无法判断`.

Use `一致` only when core intent is the same and any result differences are
non-material under the user's stated objective. Use `有条件一致` when
the conclusion depends on schema constraints, data invariants, dialect behavior, or
an explicit interpretation of the requested purpose. A shared table or metric name
is never sufficient evidence of shared intent.

Assign Chinese confidence `高`, `中`, or `低`. A concrete counterexample can disprove
equivalence; matching sample outputs cannot prove equivalence for all valid data.

## Verification

- Prefer an available dialect-aware parser, such as SQLGlot, for AST inspection and
  transpilation. Parsing or transpilation supports analysis but is not proof.
- If a safe local engine is available, run only read-only queries against synthetic
  data or an explicitly authorized test database. Do not run untrusted comparison
  SQL against production data.
- Honor known primary keys, unique constraints, foreign keys, nullability, collations,
  and numeric types when generating test rows.
- Do not use SQLite or DuckDB behavior as evidence for another dialect without
  checking the relevant semantic difference.

## Response format

Use progressive disclosure: give the decision and plain-language reason first, then
the evidence. Do not place purpose summaries or a large table before the core reason.

Start with this compact Chinese block:

```markdown
## 比对结论

**结论：** 不一致

**置信度：** 高

### 核心原因

- SQL A 统计订单数，SQL B 统计去重用户数，指标口径不同。
- SQL A 包含结束日期，SQL B 不包含，时间范围不同。
```

Apply these rules to the compact block:

- State the conclusion in one line.
- If inconsistent, list only the 1–3 highest-impact reasons. Each reason must be a
  short causal statement in the form **difference -> result or business impact**.
- Use plain business language before SQL terminology. Avoid AST vocabulary, internal
  verdict codes, long SQL fragments, and speculative edge cases here.
- If consistent, replace `核心原因` with `一致原因` and state briefly why surface
  differences do not change the purpose or result.
- If conditional or indeterminate, name the single most important missing condition
  or assumption immediately.

Then provide `## 详细对比` with the following content as applicable:

1. `SQL 目的` — one sentence each for SQL A and SQL B;
2. a compact table with Chinese columns `对比维度`, `SQL A`, `SQL B`, and `影响`;
3. `忽略的非实质差异` — dialect syntax, aliases, formatting, or proven-safe sugar;
4. `成立条件与缺失信息` — only assumptions that can change the verdict;
5. `最小反例` — only when it materially strengthens or demonstrates a mismatch.

Put material differences first in the table. Omit identical dimensions and empty
sections unless they help justify an equivalence verdict. Group cosmetic differences
instead of listing every textual change. Quote only the shortest relevant SQL
fragments; do not reproduce long queries unless needed to make the reasoning
auditable.
