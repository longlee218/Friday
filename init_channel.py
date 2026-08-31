"""Create a channel's context file before the agent has learned anything.

`derived` fills itself in once something is learned; `overrides` is created
empty here, for the operator to hand-edit — it's just YAML on disk.

Usage: `uv run init_channel.py <channel_id>`
"""

from __future__ import annotations

import sys

from friday.channel_context import ContextStore
from friday.config import load_config


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: init_channel.py <channel_id>")
    config = load_config()
    store = ContextStore(config.context.directory)
    channel_id = sys.argv[1]
    try:
        store.init_channel(channel_id)
    except FileExistsError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"created {store.path_for(channel_id)} — edit its overrides section by hand")


if __name__ == "__main__":
    main()
