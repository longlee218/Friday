"""Run just the board's API, against the live database.

The agent serves this itself; this is for looking at what is already stored
without connecting to Discord or sending anything. Read-only, so it is safe to
run beside a live agent — SQLite in WAL mode takes many readers.

It served a server-rendered page too until board ticket 01 deleted it. What is
left is the JSON API, which is what `web/` will read (board ticket 05) and what
is worth having pointed at the live database while that is being built.
"""

from __future__ import annotations

import asyncio

import uvicorn
from dotenv import load_dotenv

from friday.ops.api import bind, build_api
from friday.config import load_config
from friday.store.db import Database


async def main() -> None:
    load_dotenv()
    config = load_config()
    db = await Database.connect(config.database_path)
    status = lambda: "not connected (board only)"  # noqa: E731
    app = build_api(db=db, provider_status=status, origins=list(config.board_origins))
    sock = bind(config.board_host, config.board_port)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    print(f"api on http://localhost:{config.board_port}/api/board")
    await server.serve(sockets=[sock])


if __name__ == "__main__":
    asyncio.run(main())
