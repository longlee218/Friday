"""One-off: turn a directory of channel context files into memory rows.

The YAML files went (board `read-it-the-way-the-operator-does`, ticket 10);
what the operator had written in them becomes `origin=admin` rows:

- `base.yaml`'s keys become `fact` rows with `channel_id='*'`, true everywhere.
- a channel file's `overrides` become that channel's `fact` rows, one per key,
  written `key: value`.
- a `people:` mapping becomes `person` rows where an entry has the shape one
  needs (`name`, `role`, `team`, keyed by Discord id), and a `fact` row
  otherwise — a name and a note is still something the operator wrote.
- `derived` and `state` are not imported: they are the summariser's, and it
  rebuilds them as a `summary` row the next time the room says anything.

Every row goes through `Database.memory_add`, the one door, so a line the
instruction-shape guard refuses is reported and not written, and the rest
still lands. Run it once; a second run writes the prose rows again.

Usage: `uv run import_context_files.py [directory]` (default `context`).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from friday.kernel.config import load_config
from friday.kernel.domain.memory_guard import InstructionShaped, check_not_instruction_shaped
from friday.sdk.memory import MemoryOrigin
from friday.kernel.domain.models import FridayState, MemoryRefused
from friday.kernel.memory import registry as memory_kinds
from friday.kernel.memory import write
from friday.store.db import Database

_PERSON = ("name", "role", "team")


async def import_directory(db: Database, directory: Path) -> list[str]:
    """Write every file in `directory` as rows; return what was refused, one
    line each, naming the file and the key."""
    refused: list[str] = []
    for path in sorted(Path(directory).glob("*.yaml")):
        raw = yaml.safe_load(path.read_text()) or {}
        if path.name == "base.yaml":
            channel_id, written = "*", raw
        else:
            channel_id, written = path.stem, raw.get("overrides") or {}
        for problem in await _write(db, channel_id, written):
            refused.append(f"{path.name}: {problem}")
    return refused


async def _write(db: Database, channel_id: str, written: dict[str, Any]) -> list[str]:
    state = FridayState(channel_id=channel_id, agent="operator")
    rows: list[tuple[str, str, str, dict | None, Any]] = []
    for key, value in written.items():
        if key == "people" and isinstance(value, dict):
            for who, about in value.items():
                if isinstance(about, dict) and all(k in about for k in _PERSON):
                    data = {"discord_id": str(who), **{k: str(about[k]) for k in _PERSON}}
                    rows.append((f"people.{who}", "", memory_kinds.PERSON, data, None))
                else:
                    rows.append((f"people.{who}", f"people.{who}: {about}", memory_kinds.FACT, None, about))
        else:
            rows.append((key, f"{key}: {value}", memory_kinds.FACT, None, value))

    refused = []
    for key, text, kind, data, value in rows:
        try:
            # The value on its own as well as the written line: the guard
            # reads a sentence's first word, and `key: ` in front of a
            # directive would otherwise walk it past — which is what the
            # store checked, value by value, when these were overrides.
            check_not_instruction_shaped(value)
            written_row = await write.add(
                db, state, text, kind=kind, origin=MemoryOrigin.ADMIN, data=data
            )
        except (InstructionShaped, MemoryRefused) as why:
            refused.append(f"{key} — {why}")
            continue
        if written_row is None:
            refused.append(f"{key} — the room is at its memory ceiling")
    return refused


async def main() -> None:
    load_dotenv()
    config = load_config()
    # Memory kinds register themselves (ticket 12); the store reads the registry
    # to validate a write, so fill it before writing any row.
    memory_kinds.register_all_memory_kinds()
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "context")
    db = await Database.connect(config.database_path)
    try:
        refused = await import_directory(db, directory)
    finally:
        await db.close()
    for line in refused:
        print(f"not imported: {line}")
    print(f"imported {directory}; {len(refused)} line(s) refused")


if __name__ == "__main__":
    asyncio.run(main())
