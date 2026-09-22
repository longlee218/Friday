# 03: Approver identity

**What to build:** Only the operator can release a reply — the identity of whoever approves an outbox row is checked against `operator_id` in kernel code, not trusted from whatever button was pressed.

**Blocked by:** 01.

**Status:** done

- [x] The decider of an approval is checked against `operator_id` before the row becomes sendable
- [x] A decision by any other principal is refused
- [x] Test: a non-operator decider is rejected (guard deleted once and watched go red)
- [x] `uv run pytest -q` passes
