"""Composition root: the only place adapters are constructed."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from friday.config import load_config
from friday.db import Database
from friday.inbox import Inbox
from friday.providers import CredentialRejected
from friday.providers.discord import DiscordUserProvider

log = logging.getLogger("friday")


async def run() -> None:
    config = load_config()
    token = os.environ.get("DISCORD_USER_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_USER_TOKEN is not set. Put it in .env (see .env.example) "
            "or export it before running."
        )
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)

    db = await Database.connect(config.database_path)
    provider = DiscordUserProvider(
        token=token, capture_own_messages=config.ingest.capture_own_messages
    )
    inbox = Inbox(provider=provider, db=db, config=config.ingest)

    if config.ingest.capture_own_messages:
        log.warning(
            "capture_own_messages is ON — testing only; turn it off before the "
            "agent can reply, or it will answer itself"
        )
    log.info(
        "watching %d channel(s) for %s",
        len(config.ingest.watched_channels),
        ", ".join(sorted(config.ingest.mention_types)),
    )
    try:
        smoke = os.environ.get("SMOKE_ECHO") == "1"
        if smoke:
            log.warning("SMOKE_ECHO is ON — replying without any review gate")
        async for event in inbox.stream():
            log.info(
                "captured %s from %s in %s",
                event.mention_type,
                event.author_name,
                event.channel_id,
            )
            # Temporary end-to-end check that outbound works. Ticket 06 replaces
            # this with the real path, where nothing posts without approval.
            # The text carries the mention markup ("<@123> Hi there"), so
            # match on a substring rather than the whole message.
            if smoke and "hi there" in event.text.lower():
                await provider.reply(event, "What'sapp")
    except CredentialRejected as exc:
        raise SystemExit(f"Discord rejected the credential: {exc}") from exc
    finally:
        await db.close()


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
