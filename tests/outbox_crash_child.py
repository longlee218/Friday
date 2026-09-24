"""Child process for the ticket-07 outbox crash test in `test_workflow_port.py`.

A real kill is a separate process that `os._exit`s between the channel call and
the record of it — the one moment the whole ticket is about. The sender writes a
marker (the "channel call happened" side effect) then exits hard, mid-DBOS-step,
before `deliver_once` can mark the row `sent`. The parent then relaunches DBOS on
the same system database and the same application database: recovery re-enters
the PENDING delivery workflow, `deliver_once` reads the `dispatching` marker its
own crashed run committed, and — on a channel that cannot dedupe — routes the row
to `delivery_unknown` without sending again.

Usage (run by the test):
    python tests/outbox_crash_child.py <sysdb> <app_db> <marker> <row_id>
"""

from __future__ import annotations

import asyncio
import os
import sys

from friday.kernel.outbox import Outbox
from friday.store.db import Database
from friday.kernel.dag import adapter


class Crashing:
    """A non-idempotent channel that dies mid-send: it records that it was
    called (the side effect that must not repeat) then kills the process before
    the outbox can write down that it succeeded."""

    supports_idempotency = False

    def __init__(self, marker: str) -> None:
        self._marker = marker

    async def send(self, row) -> str:
        with open(self._marker, "a") as fh:
            fh.write("a\n")  # the channel call — must happen exactly once
        os._exit(1)  # power-loss between the call and the write


async def _go(sysdb: str, app_db: str, marker: str, row_id: int) -> None:
    db = await Database.connect(app_db, create=False)
    adapter.launch("friday-wf-test", sysdb)
    box = Outbox(
        db=db,
        senders={"discord_user": Crashing(marker)},
        durable=adapter.deliver_outbound,
    )
    adapter.register_outbox(box.deliver_once)
    # The row is `dispatching` after this marks it and before the sender exits;
    # the process never returns from the send.
    await adapter.deliver_outbound(row_id, 0)
    os._exit(2)  # reached only if the sender did not crash — fail loudly


def _main() -> None:
    sysdb, app_db, marker, row_id = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    asyncio.run(_go(sysdb, app_db, marker, row_id))


if __name__ == "__main__":
    _main()
