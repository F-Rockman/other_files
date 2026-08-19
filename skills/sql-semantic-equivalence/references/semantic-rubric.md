# Semantic comparison rubric

Use this rubric before issuing a verdict. Compare in the order below because an early
grain or population mismatch often explains later differences.

## 1. Statement purpose and output contract

- Are both statements reads, or do they mutate or define data?
- Do output columns represent the same business concepts, even if names differ?
- Are types, units, sign conventions, precision, and default values compatible?
- Is row order part of the consumer contract? It usually is not unless paired with a
  limit, window, array/list aggregation, cursor, or an explicit downstream need.

## 2. Entity, grain, and cardinality

Identify what one output row represents. Compare grouping keys, implicit detail
columns, deduplication, and cardinality expansion. Typical mismatches include:

- user grain versus order grain;
- daily versus monthly grain;
- grouped results versus windowed detail rows;
- one row per key versus one row per matching relationship;
- `DISTINCT` masking duplicate-producing joins.

If grain differs, the core intent is normally not the same unless the user explicitly
defines that difference as immaterial.

## 3. Population and row eligibility

Reduce filters to logical conditions while retaining SQL three-valued logic. Compare:

- predicate columns, operators, constants, and parameter meanings;
- inclusive/exclusive boundaries;
- `WHERE`, `ON`, `HAVING`, and `QUALIFY` placement;
- correlated predicate scope;
- tenant, status, soft-delete, security, or validity filters;
- whether filters occur before or after aggregation/windowing.

Predicate reordering is harmless only for deterministic, side-effect-free expressions
whose evaluation errors and coercions do not make order observable.

## 4. Sources, lineage, joins, and relationship assumptions

Compare source relations and the lineage of each output expression. For every join,
check type, key, additional predicates, and expected relationship cardinality.

Do not declare the following equivalent without supporting constraints:

- `JOIN` versus `EXISTS`: a join can duplicate left rows;
- `LEFT JOIN` versus `INNER JOIN`;
- a right-table filter in `ON` versus the same filter in `WHERE` after a left join;
- joining on a declared unique key versus an unconstrained column;
- omitting a table that appears redundant without a foreign-key/existence guarantee.

## 5. Measures and aggregation algebra

Compare exact metric definitions, not aggregate function names alone:

- `COUNT(*)`, `COUNT(column)`, and `COUNT(DISTINCT column)`;
- denominator and numerator populations for ratios;
- weighted versus unweighted averages;
- row-level calculation before aggregation versus aggregate-level calculation;
- conditional aggregation and its `ELSE` branch;
- integer, decimal, and floating-point division;
- overflow, rounding, precision, and implicit casts;
- empty-group behavior, including zero versus null.

Algebraic rewrites are equivalent only under the necessary null, type, and domain
assumptions.

## 6. Null and three-valued logic

Track null introduction, propagation, filtering, substitution, and comparison.
Particular traps include:

- `NOT IN` versus `NOT EXISTS` when the subquery can contain null;
- `=` versus null-safe equality (`IS NOT DISTINCT FROM`, `<=>`, or dialect analogue);
- `COUNT(*)` versus `COUNT(nullable_column)`;
- `COALESCE` changing missing-data meaning;
- null ordering in ordered or windowed results;
- predicates on nullable columns becoming `UNKNOWN` rather than `FALSE`.

## 7. Bag, set, and set-operation behavior

SQL query results normally preserve duplicates. Compare:

- `UNION` versus `UNION ALL`;
- `INTERSECT`/`EXCEPT` duplicate rules by dialect;
- `DISTINCT` scope;
- deduplication before versus after aggregation;
- whether joins or unnests multiply rows.

Column position, coercion, and null equality in set operations can also affect results.

## 8. Time semantics

Compare:

- timestamps versus dates and truncation granularity;
- inclusive closed ranges versus half-open ranges;
- session, source, and destination time zones;
- daylight-saving transitions;
- calendar week/month definitions and fiscal calendars;
- current-time functions, snapshot time, and slowly changing dimensions;
- string-to-time parsing and invalid-input behavior.

Treat similarly named date functions across dialects as unverified until their units,
week rules, boundaries, and time-zone behavior align.

## 9. Windows, ordering, limits, and nondeterminism

Compare partition keys, ordering expressions, frames, and tie handling. Check:

- `ROWS` versus `RANGE` frames;
- omitted frames and dialect defaults;
- `ROW_NUMBER`, `RANK`, and `DENSE_RANK`;
- deterministic tie breakers;
- filter-after-window behavior;
- `LIMIT`/`TOP`/`FETCH`, sampling, and unordered row selection;
- volatile functions such as random values, generated IDs, or current time.

## 10. Dialect and execution context

Record any conclusion that depends on:

- identifier case-folding or quoting;
- collation and character comparison;
- implicit casts and type precedence;
- integer division and overflow;
- alias visibility rules;
- boolean coercion;
- materialized CTE or optimizer behavior when volatile expressions are present;
- session variables, permissions, row-level security, or transaction snapshot.

Performance-plan differences are non-material unless they change observable behavior,
side effects, failure behavior, or the user's stated purpose.

## Common unsafe equivalence shortcuts

Never assume these pairs are equivalent without checking their conditions:

| Pair | Why it can differ |
| --- | --- |
| `BETWEEN a AND b` / `>= a AND < b` | Upper boundary differs |
| `NOT IN` / `NOT EXISTS` | Nulls change truth values |
| `JOIN` / `EXISTS` | Join matches can duplicate rows |
| `UNION` / `UNION ALL` | Duplicate elimination differs |
| `COUNT(*)` / `COUNT(x)` | Null `x` values are skipped |
| `SUM(CASE ... ELSE 0 END)` / aggregate `FILTER` | Empty or unmatched groups may yield zero versus null |
| `LEFT JOIN` filter in `ON` / in `WHERE` | The latter can reject unmatched rows |
| CTE / inline subquery | Volatile evaluation or materialization can differ |
| `DATE(timestamp)` across dialects | Time-zone conversion can differ |
| unordered `LIMIT` queries | Selected rows are nondeterministic |

## Assumption register

State assumptions precisely and connect each one to the conclusion. Useful categories:

- schema: primary/foreign/unique keys, nullability, checks;
- data: at-most-one match, nonnegative values, no nulls, valid ranges;
- business: metric definition, intended population, acceptable grain;
- dialect: engine and version, type/coercion/time behavior;
- execution: parameters, time zone, collation, snapshot, permissions.

Prefer `EQUIVALENT_UNDER_ASSUMPTIONS` over silently treating an observed data pattern
as a permanent invariant.
