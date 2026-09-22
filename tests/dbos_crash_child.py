"""Child process for the box-8 crash test in `test_workflow_port.py`.

A real kill is a separate process that `os._exit`s mid-workflow — the same
shape the DBOS spike used, and the only faithful "kill mid-workflow" (an
in-process `destroy()` cannot kill a live workflow coroutine). Both the child
here and the parent test register the identical graph by name, so DBOS recovery
in the parent re-enters `_run_graph` for the workflow this child left PENDING.

Usage (run by the test): `python tests/dbos_crash_child.py <sysdb> <marker> <wfid>`
"""

from __future__ import annotations

import os
import sys
import time

from friday.domain.actions import Ask
from friday.sdk.workflow import DAG, Deps, Edge, Node, envelope
from friday.workflow import adapter

CRASH_DAG = "crash-box8"


def build_and_register(marker: str) -> None:
    """Register the graph both processes share. `ask` appends to the marker file
    (the side effect that must not repeat on resume) then suspends on `Ask`."""

    async def ask(state: object, deps: Deps):
        if deps.answers:  # re-run after the answer arrives
            return envelope("ok", answer=deps.answers[-1])
        with open(marker, "a") as fh:
            fh.write("a\n")  # the side effect that must NOT repeat on resume
        return Ask("waiting for a human")

    async def after(state: object, deps: object):
        return envelope("ok", done=True)

    async def deps_factory(scope_key) -> Deps:
        return Deps(extra=dict(scope_key))

    dag = DAG(
        name=CRASH_DAG,
        nodes=(Node(name="ask", run=ask), Node(name="after", run=after)),
        edges=(Edge("ask", "after"),),
    )
    adapter.register_graph(dag, deps_factory)


def _main() -> None:
    import asyncio

    sysdb, marker, wfid = sys.argv[1], sys.argv[2], sys.argv[3]
    from dbos import DBOS, DBOSConfig

    DBOS.destroy(destroy_registry=False)
    cfg: DBOSConfig = {"name": "friday-wf-test", "system_database_url": f"sqlite:///{sysdb}"}
    DBOS(config=cfg)
    DBOS.launch()
    build_and_register(marker)

    async def go() -> None:
        await adapter.start(CRASH_DAG, {}, workflow_id=wfid)
        # Wait until `ask` has run and the workflow is suspended on recv, then
        # kill the process hard — power-loss while waiting for the operator.
        for _ in range(500):
            if os.path.exists(marker) and open(marker).read().count("a") >= 1:
                time.sleep(0.2)  # let the recv suspension checkpoint
                os._exit(1)
            await asyncio.sleep(0.02)
        os._exit(2)  # never reached the suspend — fail loudly

    asyncio.run(go())


if __name__ == "__main__":
    _main()
