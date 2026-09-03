"""Put a message into the queue by hand, as if somebody had reported it.

    uv run poke.py "API checkout trả 500, correlationId abc-123"

The agent has to be running: this only writes the row. Triage picks it up on
its next pass, and everything after that is the real pipeline — extraction,
the graph, the responder, the outbox, a real message in the real channel.

**Why this exists.** The system will not take work from the watched account,
by design: the operator typing is what *ends* a task, so their own messages
never open one. That rule has no exception for "but I meant this one" — a
self-mention used to be allowed behind a configuration switch, and with it on
the agent's own replies came back through the gateway and opened a task each,
every minute, in a real channel (ticket 37 removed it). So there is no way to
test by talking to yourself, and this is the way that does not reopen that
door: it skips the gateway and the scope check, and nothing else.

The one thing it fakes is *who sent it*. Everything downstream — whether the
turn is finished, what type it is, what parameters it has, what gets asked,
what waits for approval — runs exactly as it does for a real reporter.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from friday.config import load_config
from friday.domain.models import InboundEvent, MentionType
from friday.store.db import Database


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text", help="what the reporter said")
    ap.add_argument(
        "--channel",
        help="channel id; defaults to the only watched channel, if there is one",
    )
    ap.add_argument("--author", default="poke-reporter", help="who to say it is from")
    ap.add_argument("--name", default="Người thử", help="their display name")
    ap.add_argument(
        "--ago",
        type=int,
        default=120,
        help="seconds ago, so the turn already counts as finished (default 120)",
    )
    args = ap.parse_args()

    load_dotenv()  # config.yaml refers to ${...}; run_agent.py does this too
    config = load_config()
    watched = sorted(config.ingest.watched_channels)
    channel = args.channel or (watched[0] if len(watched) == 1 else None)
    if channel is None:
        print(f"say which channel: --channel one of {watched}", file=sys.stderr)
        return 2
    if channel not in config.ingest.watched_channels:
        print(f"{channel} is not watched; the pool would never see it", file=sys.stderr)
        return 2

    event = InboundEvent(
        provider="discord",
        provider_message_id=f"poke-{int(datetime.now(timezone.utc).timestamp())}",
        channel_id=channel,
        thread_id=None,
        author_id=args.author,
        author_name=args.name,
        text=args.text,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=args.ago),
        mention_type=MentionType.DIRECT,
        is_own=False,
    )

    # Same override migrations use, for the same reason: try it against a
    # throwaway copy before poking the database the running agent is on.
    path = os.environ.get("FRIDAY_DB") or config.database_path
    db = await Database.connect(path, create=False)
    try:
        # `record_conversation` first: an untracked conversation is one the
        # rest of the system will not act in.
        await db.record_conversation(event)
        if not await db.record_message(event):
            print("already recorded — nothing to do", file=sys.stderr)
            return 1
    finally:
        await db.close()

    print(f"queued {event.provider_message_id} in {channel} ({path})")
    print(f"  {args.name}: {args.text}")
    print("watch it with: docker compose logs -f   (or the board on :8086)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
