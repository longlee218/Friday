# Execution plan — five groups, three lanes (2026-09-18)

Read from every ticket's `Blocked by` line on 2026-09-18, after three
corrections made the same day: 04 no longer waits on 02, 05 no longer waits
on 07, and 16 is done except the reporter-delay measurement.

## The groups

| Group | Tickets, in order | Blocked from outside by | What it delivers |
|---|---|---|---|
| **A. Platform fixes** | 12 → 13 → 11 | nothing | three defects/gaps in code that exists today: approval per row, a pool that does not stall, one invoke with timeout/retry in the engine |
| **B. Knowledge store** | 09 → 10 | nothing (merge after 12 — both add a migration) | twelve kinds in SQLite, the admin form, YAML gone |
| **C. Prove it on real cases** | 16 (done) → 00 | 12 | the slice: five remembered cases through the real pool, outbox and board |
| **D. The api_issue graph** | 01 → (02 ∥ 03) → 04 → 05 → 06 → 08 | 00 (its lessons), 09 (for 01's routing half) | the graph widened from the slice's thin versions |
| **E. Measure, then extend** | 14 → 15 | 05, 06 (for 14); 09, 11 (for 15) | the eval, its baseline, then the Collector — or its ceiling at 0 |
| **Human** | 07; the five cases for 00; eval labels for 14 | 09 for 07's form; nothing for the rest | knowledge rows, runbooks, ground truth |

Inside a group the tickets block each other; across groups they do not,
except at the named points.

## Three lanes that can run at once

```
lane 1  A: 12 ─▶ 13 ─▶ 11 ───────────────▶ C: 00 ─▶ D: 01 ─▶ 02 ∥ 03 ─▶ 04 ─▶ 05 ─▶ 06 ─▶ 08
              │                                  ▲                             │
lane 2  B:    └─(merge after 12)─ 09 ─▶ 10       │ (01's routing half)         └─▶ E: 14 ─▶ 15
                                   └─────────────┘
human   07 on research/03-seed-rows.md now · 5 cases for 00 now · labels when 14 lands
```

- **Lane 1 is the critical path.** 12 first because it is a real defect and
  it blocks 00. 13 and 11 before 00 because the slice runs through the pool
  and the engine and should hit the fixed versions.
- **Lane 2 is independent work** that only has to land before D's first
  ticket needs rows. It can be built in parallel with lane 1 in its own
  worktree.
- **The human lane has no reason to wait.** The seed rows are drafted; the
  five cases are needed by 00; nothing about either depends on code.

## Why this order and not the numbers

- **Fix what exists before building on it.** A holds the only defects found
  in code that already runs (approval per task; a sequential pool); every
  later group inherits them otherwise.
- **The slice before the graph.** 00 builds thin first versions of what 01,
  02, 04, 05 and 06 will widen. D extends the slice rather than starting
  over; the slice may still be thrown away if it proves the shape wrong,
  which is the point of running it first.
- **Measure before extending.** 15 is the one ticket whose value is
  uncertain; it waits for 14's baseline and ships with its ceiling at 0 if
  the numbers say so.

## Conflicts to plan for when lanes run in parallel

- **Migrations.** 12 and 09 each add an Alembic revision. Merge 12 first;
  09 rebases and re-points its `down_revision` at 12's head.
  `tests/test_migrations.py` catches a fork.
- **`friday/tasks/pool.py`** is touched by 12 (`_propose`, `_route`), 13
  (`run_once`) and 11 (`_walk`). That is why A runs in sequence inside one
  lane rather than as three parallel tickets.
- **`friday/store/db.py`** is touched by 12 (outbox predicate) and 09 (memory
  methods). Different functions; a rebase, not a redesign.

## How each ticket is done (from `CLAUDE.md`, "Verifying a change")

1. Whole suite, not a subset. 2. A `code-review` subagent reads it.
3. Every new guard deleted once and watched go red. 4. Anything upstream of
`friday/triage/prompt.py` also runs the triage eval — this applies to 10.
One commit per ticket, board status updated in the same commit.
