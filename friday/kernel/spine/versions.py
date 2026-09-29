"""The pass's `plan` step: which plan this pass runs, and every version the
Planner writes, stored (build-the-spine ticket 14; board `domains-plug-in`
ticket 14 §5, §6, §9).

```
this pass already planned (a crash re-run)   → the version it stored
hand-back                                    → a fresh plan (not counted): a replan
                                               of the last frozen one, or a first plan
no frozen plan yet                           → a first plan
placement changed since the last frozen one  → replan (cause `reply`, counted);
                                               max_replans spent → HandOver replans_exhausted
else                                         → the last frozen plan, unchanged
```

Replans are counted from the plans table: versions of cause `replan` or
`reply` since the last `hand_back`. Every version — frozen, or refused with
its gate errors — is a row, written once (`put_plan`). Just over 200 lines:
the decision above and its two writers are one unit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from friday.kernel.spine.plan import plan_from_json, plan_json
from friday.kernel.spine.plan_gate import Frozen
from friday.kernel.spine.planner import PlannerFailed, Planning, plan, replan
from friday.kernel.spine.runner import stored_results
from friday.sdk.actions import HandOver, Replan

__all__ = ["COUNTED", "Planned", "choose_plan", "write_replan"]

#: Plan causes that count toward the contract's `max_replans`.
COUNTED = ("replan", "reply")


@dataclass(frozen=True, slots=True)
class Planned:
    """What the `plan` step decided: a plan to run, or the hand-over instead;
    the replans used since the last hand-back; and when that hand-back was
    planned (`None`: never) — asks are counted from there."""

    frozen: Frozen | None
    outcome: HandOver | None
    replans_used: int
    asks_since: datetime | None


def _as_planned(row: Any) -> Frozen | HandOver:
    if row.hash is not None:
        return Frozen(plan=plan_from_json(row.body), plan_hash=row.hash)
    last = "; ".join(row.gate_errors[-1]) if row.gate_errors else "no plan"
    return HandOver(f"planner_failed: plan v{row.version}: {last}")


async def _store(
    db: Any,
    written: Frozen | PlannerFailed,
    *,
    task_id: int,
    version: int,
    cause: str,
    identity: tuple,
    pass_no: int,
) -> Any:
    """Store the version the Planner wrote and return the stored row — a
    crash that re-ran the Planner keeps the version stored first."""
    body: dict | None
    if isinstance(written, Frozen):
        replaces, body = written.plan.replaces, plan_json(written.plan)
        hash_, errors = written.plan_hash, []
    else:
        tried = next((w for w in reversed(written.plans) if w is not None), None)
        replaces = tried.replaces if tried is not None else None
        body = plan_json(tried) if tried is not None else None
        hash_, errors = None, [list(e) for e in written.errors]
    row = await db.put_plan(
        task_id=task_id,
        version=version,
        replaces=replaces,
        cause=cause,
        placement=list(identity),
        body=body,
        hash=hash_,
        gate_errors=errors,
        pass_no=pass_no,
    )
    return row


async def choose_plan(
    db: Any,
    planning: Planning,
    agents: Mapping[str, Any],
    *,
    pass_no: int,
    cause: str,
) -> Planned:
    """The plan pass `pass_no` runs; `cause` is why the pass started."""
    task_id, context = planning.task_id, planning.intake
    rows = await db.plans(task_id)
    back = max((i for i, r in enumerate(rows) if r.cause == "hand_back"), default=None)
    since = rows[back].created_at if back is not None else None
    used = sum(r.cause in COUNTED for r in rows[back or 0 :])

    def planned(
        chosen: Frozen | HandOver, replans: int = used, asks_from=since
    ) -> Planned:
        if isinstance(chosen, Frozen):
            return Planned(chosen, None, replans, asks_from)
        return Planned(None, chosen, replans, asks_from)

    mine = [r for r in rows if r.pass_no == pass_no]
    if mine:
        return planned(_as_planned(mine[0]))
    frozen = [r for r in rows if r.hash is not None]
    last = frozen[-1] if frozen else None
    version = rows[-1].version + 1 if rows else 1
    store = lambda written, why: _store(
        db,
        written,
        task_id=task_id,
        version=version,
        cause=why,
        identity=context.identity,
        pass_no=pass_no,
    )

    if cause == "hand_back":
        # A fresh run on the old ground (§9): replans and asks count from
        # here, and the Planner sees the last plan and its results.
        if last is None:
            written = await plan(planning, version=version)
        else:
            written = await _replan_from(
                db,
                planning,
                agents,
                last,
                Replan(
                    reason="the operator handed this task back; plan it afresh",
                    found="",
                ),
                version,
            )
        row = await store(written, cause)
        return planned(_as_planned(row), 0, row.created_at)
    if last is None:
        written = await plan(planning, version=version)
        return planned(_as_planned(await store(written, "first")))
    if tuple(last.placement) == context.identity:
        return planned(_as_planned(last))
    if used >= planning.action.contract.limits.max_replans:
        return planned(HandOver("replans_exhausted: the reply moved where this runs"))
    written = await _replan_from(
        db,
        planning,
        agents,
        last,
        Replan(
            reason="the reporter's reply moved where this runs",
            found=f"placement was {list(last.placement)}, "
            f"is now {list(context.identity)}",
        ),
        version,
    )
    return planned(_as_planned(await store(written, "reply")), used + 1)


async def _replan_from(
    db: Any,
    planning: Planning,
    agents: Mapping[str, Any],
    row: Any,
    signal: Replan,
    version: int,
) -> Frozen | PlannerFailed:
    """Replan from a stored frozen version, with what its steps came to under
    the placement it was planned for."""
    current = _as_planned(row)
    assert isinstance(current, Frozen)
    results = await stored_results(db, current, agents, tuple(row.placement))
    return await replan(planning, current, results, signal, version=version)


async def write_replan(
    db: Any,
    planning: Planning,
    current: Frozen,
    results: Mapping[str, Any],
    signal: Replan,
    *,
    pass_no: int,
) -> Frozen | HandOver:
    """The runner's `Steps.planner`: the next version after `current`, stored
    (cause `replan`). A crash re-run of the pass finds the version this pass
    already wrote to replace `current` and runs that instead."""
    rows = await db.plans(planning.task_id)
    for row in rows:
        if row.pass_no == pass_no and row.replaces == current.plan_hash:
            return _as_planned(row)
    version = rows[-1].version + 1  # `current` came from a stored row
    written = await replan(planning, current, results, signal, version=version)
    row = await _store(
        db,
        written,
        task_id=planning.task_id,
        version=version,
        cause="replan",
        identity=planning.intake.identity,
        pass_no=pass_no,
    )
    return _as_planned(row)
