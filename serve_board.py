"""Run just the board's API, against the live database.

The agent serves this itself; this is for looking at what is already stored,
and writing the operator's memory rows, without connecting to Discord or
sending anything. Safe to run beside a live agent — SQLite in WAL mode takes
many readers, and what this writes is rows through the same store the agent
reads them from.

It served a server-rendered page too until board ticket 01 deleted it. What is
left is the JSON API, which is what `web/` reads (board ticket 05) and what is
worth having pointed at the live database while that is being built.

**Not read-only, and it says so here because it stopped being true.** This
docstring claimed "read-only, so it is safe to run beside a live agent" for
exactly as long as it took a review to notice that the commit below had wired
a write path in — a channel file's overrides then, the operator's memory rows
since the files went. A write path is why `check_exposure` is called here as
well as in `run_agent.py`: two entrypoints bind the same `config.board_host`,
and hardening one of them is hardening none.
"""

from __future__ import annotations

import asyncio

import uvicorn
from dotenv import load_dotenv

from friday.kernel.config import load_config
from friday.kernel.ops.api import bind, build_api, check_exposure
from friday.kernel.triage.runner import CONFIDENCE_THRESHOLD
from friday.store.db import Database


async def main() -> None:
    load_dotenv()
    config = load_config()
    # The board's memory form reads the kind registry (ticket 12); fill it so a
    # board served on its own offers the same kinds the agent's does.
    from friday.kernel.memory.registry import register_all_memory_kinds

    register_all_memory_kinds()
    # `/api/actions` reads the task-type registry (build-the-spine ticket 02);
    # fill it from the configuration alone, the same way the agent's boot does.
    from friday.kernel.dag.router import check_graphs

    check_graphs(config)
    check_exposure(config.board_host)
    db = await Database.connect(config.database_path)
    status = lambda: "not connected (board only)"
    # Writable, like the agent's own board: this is the copy pointed at the
    # live database while `web/` is being built, and a page that cannot write
    # is not the page being built.
    app = build_api(
        db=db,
        provider_status=status,
        origins=list(config.board_origins),
        # Triage's own constant: which knobs triage has is triage's.
        repo_root=config.repo_root or None,
        confidence_threshold=CONFIDENCE_THRESHOLD,
    )
    sock = bind(config.board_host, config.board_port)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    print(f"api on http://localhost:{config.board_port}/api/board")
    await server.serve(sockets=[sock])


if __name__ == "__main__":
    asyncio.run(main())
