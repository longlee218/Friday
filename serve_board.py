"""Run just the board's API, against the live database.

The agent serves this itself; this is for looking at what is already stored,
and editing a channel's context, without connecting to Discord or sending
anything. Safe to run beside a live agent — SQLite in WAL mode takes many
readers, and the one thing this writes is a YAML file the agent re-reads only
when asked to.

It served a server-rendered page too until board ticket 01 deleted it. What is
left is the JSON API, which is what `web/` reads (board ticket 05) and what is
worth having pointed at the live database while that is being built.

**Not read-only, and it says so here because it stopped being true.** This
docstring claimed "read-only, so it is safe to run beside a live agent" for
exactly as long as it took a review to notice that the commit below had wired
a `ContextStore` in. A write path is why `check_exposure` is called here as
well as in `run_agent.py`: two entrypoints bind the same `config.board_host`,
and hardening one of them is hardening none.
"""

from __future__ import annotations

import asyncio
import os

import uvicorn
from dotenv import load_dotenv

from friday.memory.channel_context import ContextStore
from friday.ops.api import bind, build_api, check_exposure
from friday.config import load_config
from friday.store.db import Database
from friday.triage.runner import TriageRunner


async def main() -> None:
    load_dotenv()
    config = load_config()
    check_exposure(config.board_host, token=os.environ.get("BOARD_TOKEN"))
    db = await Database.connect(config.database_path)
    status = lambda: "not connected (board only)"  # noqa: E731
    # Writable, like the agent's own board: this is the copy pointed at the
    # live database while `web/` is being built, and a page that cannot write
    # is not the page being built.
    app = build_api(
        db=db,
        provider_status=status,
        origins=list(config.board_origins),
        context_store=ContextStore.build(config),
        # Asked of the runner rather than read out of config, for the reason
        # `run_agent.py` is held to: which knobs triage has is triage's.
        confidence_threshold=(
            await TriageRunner.build(config, db=db)
        ).confidence_threshold,
    )
    sock = bind(config.board_host, config.board_port)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    print(f"api on http://localhost:{config.board_port}/api/board")
    await server.serve(sockets=[sock])


if __name__ == "__main__":
    asyncio.run(main())
