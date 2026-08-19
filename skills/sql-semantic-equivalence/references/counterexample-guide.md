# Minimal counterexample guide

Use a counterexample to demonstrate a suspected semantic mismatch. A single valid
database state that produces different observable results disproves universal result
equivalence.

## Construct the example

1. Isolate one claimed difference. Do not mix null, duplicate, boundary, and join
   effects unless the interaction itself is the issue.
2. Define only the columns needed by the relevant expressions.
3. Add the fewest rows that trigger the difference while honoring all known keys,
   foreign keys, checks, nullability, and business invariants.
4. Fix parameters, time zone, collation, and current time when they matter.
5. Evaluate each query under its actual dialect, or clearly label manual reasoning or
   cross-dialect emulation.
6. Show the differing output and explain why that difference affects the verdict.

## Useful witness patterns

| Suspected mismatch | Minimal witness |
| --- | --- |
| `COUNT(*)` vs `COUNT(x)` | One eligible row with `x IS NULL` |
| `COUNT(x)` vs `COUNT(DISTINCT x)` | Two eligible rows sharing the same non-null `x` |
| `JOIN` vs `EXISTS` | One left row with two matching right rows |
| left join predicate placement | One left row with no qualifying right row |
| `NOT IN` vs `NOT EXISTS` | One null in the subquery result and one nonmatching outer value |
| `UNION` vs `UNION ALL` | The same projected row emitted by both inputs |
| inclusive vs exclusive time bound | One row exactly on the boundary |
| window tie handling | Two rows tied on all stated ordering keys |
| integer vs decimal division | Values whose quotient has a fractional part |
| time-zone conversion | A timestamp near midnight or a daylight-saving transition |

## Evidence standard

- A valid witness supports `NOT_EQUIVALENT` even if existing sample data happens to
  produce matching output.
- Failed attempts to find a witness are not proof of equivalence.
- Property-based or bounded differential testing raises confidence but still covers
  only the tested domain.
- If a witness violates an asserted invariant, discard it and state that equivalence
  depends on that invariant.

## Reporting

Present the smallest input tables and the two outputs. Keep the explanation causal:

> The right table has two matches for one key. SQL A's join emits two rows, while SQL
> B's `EXISTS` emits the left row once; therefore counts and downstream aggregates can
> diverge unless the join key is unique.

If execution is unavailable, provide the witness and expected outputs as reasoned
evidence, explicitly marking them as unexecuted.
