"""Composition root: the only place adapters are constructed."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from friday.config import load_config
from friday.db import Database
from friday.inbox import Inbox
from friday.providers.discord import DiscordUserProvider

log = logging.getLogger("friday")


async def run() -> None:
    config = load_config()
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)

    db = await Database.connect(config.database_path)
    provider = DiscordUserProvider(token=os.environ["DISCORD_USER_TOKEN"])
    inbox = Inbox(provider=provider, db=db, config=config.ingest)

    log.info(
        "watching %d channel(s) for %s",
        len(config.ingest.watched_channels),
        ", ".join(sorted(config.ingest.mention_types)),
    )
    try:
        async for event in inbox.stream():
            log.info(
                "captured %s from %s in %s",
                event.mention_type,
                event.author_name,
                event.channel_id,
            )
    finally:
        await db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
