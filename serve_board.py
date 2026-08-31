"""Run just the board, against the live database.

The agent serves this itself; this is for looking at what is already stored
without connecting to Discord or sending anything. Read-only, so it is safe to
run beside a live agent — SQLite in WAL mode takes many readers.
"""

from __future__ import annotations

import asyncio

import uvicorn
from dotenv import load_dotenv

from friday.api import build_api
from friday.board import build_board
from friday.config import load_config
from friday.db import Database


async def main() -> None:
    load_dotenv()
    config = load_config()
    db = await Database.connect(config.database_path)
    status = lambda: "not connected (board only)"  # noqa: E731
    app = build_api(db=db, provider_status=status, origins=list(config.board_origins))
    app.mount("/", build_board(db=db, provider_status=status))
    server = uvicorn.Server(
        uvicorn.Config(app, host=config.board_host, port=config.board_port,
                       log_level="warning")
    )
    print(f"board on http://localhost:{config.board_port}")
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
