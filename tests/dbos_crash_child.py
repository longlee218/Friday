"""Child process for the box-8 crash test in `test_workflow_port.py`.

A real kill is a separate process that `os._exit`s mid-workflow — the same
shape the DBOS spike used, and the only faithful "kill mid-workflow" (an
in-process `destroy()` cannot kill a live workflow coroutine). Both the child
here and the parent test register the identical graph by name, so DBOS recovery
in the parent re-enters `_run_graph` for the workflow this child left PENDING.
The child dies inside `after`, which waits for a go file only the parent
writes (build-the-spine ticket 14: no workflow suspends on `recv` any more).

Usage (run by the test): `python tests/dbos_crash_child.py <sysdb> <marker> <wfid>`
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from friday.kernel.dag import adapter
from friday.sdk.workflow import DAG, Deps, Edge, Node, envelope

CRASH_DAG = "crash-box8"


def build_and_register(marker: str) -> None:
    """Register the graph both processes share. `ask` appends to the marker file
    (the side effect that must not repeat on resume); `after` waits for the go
    file, which only the restarted parent writes."""
    import asyncio

    async def ask(state: object, deps: Deps):
        with open(marker, "a") as fh:
            fh.write("a\n")  # the side effect that must NOT repeat on resume
        return envelope("ok", asked=True)

    async def after(state: object, deps: object):
        while not os.path.exists(marker + ".go"):
            await asyncio.sleep(0.02)
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
    adapter.launch("friday-wf-test", sysdb)
    build_and_register(marker)

    async def go() -> None:
        await adapter.start(CRASH_DAG, {}, workflow_id=wfid)
        # Wait until `ask` has run and `after` is waiting, then kill the
        # process hard — power-loss mid-run.
        for _ in range(500):
            if os.path.exists(marker) and Path(marker).read_text().count("a") >= 1:
                time.sleep(0.2)  # let `ask`'s result checkpoint
                os._exit(1)
            await asyncio.sleep(0.02)
        os._exit(2)  # never reached the suspend — fail loudly

    asyncio.run(go())


if __name__ == "__main__":
    _main()
