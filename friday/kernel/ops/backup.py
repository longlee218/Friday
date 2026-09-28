"""The daily backup of both SQLite files, and the restore behind it.

Friday keeps two SQLite files on the operator's machine (§12.1): the
application database and DBOS's workflow **system** database. They are one
state seen from two sides — a task and the durable workflow driving it — so a
backup that caught one without the other could restore a task whose workflow is
gone, or a workflow waiting on a task that never existed. This copies **both**,
once a day, and keeps the last few.

**Online, through SQLite's own backup API.** `sqlite3.Connection.backup` takes
a transaction-consistent snapshot while the process keeps running and WAL
writers stay active — no stop-the-world, and no half-written page. The copy is
a standalone database (not a WAL pair), so a restore is a plain file copy.

**Restore is a stopped-process operation** (`restore`, and the command in
`docs/DESIGN.md` § Backup): copy a day's two files back over the live paths
while the agent is not running. Never over a running agent — that is the one
thing the single-instance lock (§12.1) exists to prevent.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["Backup", "databases", "restore"]

log = logging.getLogger(__name__)

#: SQLite's sidecars beside a WAL-mode database. A restore must clear these, or
#: SQLite replays a stale one against the freshly restored file — see `restore`.
_SIDECARS = ("-wal", "-shm")


def databases(database_path: str) -> list[str]:
    """The two files that are one state: the application db and DBOS's workflow
    system db beside it (`<db>.system.db`). One place derives the pair, so the
    composition root and the restore command name the same files (§12.1)."""
    return [database_path, str(Path(database_path).with_suffix(".system.db"))]


def _dest(backup_dir: Path, src: Path, stamp: str) -> Path:
    """`friday.db` -> `<backup_dir>/friday.20260924.db`: the stem, the day, the
    suffix — so both files of one day share a stamp and sort together."""
    return backup_dir / f"{src.stem}.{stamp}{src.suffix}"


class Backup:
    """A daily online backup of a set of SQLite files, kept to a retention
    count. Built by the composition root with the application and system
    databases; the heartbeat asks it `run_if_due` each beat."""

    def __init__(self, *, sources: list[str], backup_dir: str, keep: int) -> None:
        #: The files to back up together — the application db and the DBOS
        #: system db. Only those that exist are copied; a system db that has
        #: not been created yet is simply skipped until it is.
        self._sources = [Path(s) for s in sources]
        self._dir = Path(backup_dir)
        #: How many days of backups to keep. `<= 0` turns backups off — the one
        #: place that decision is read, so the heartbeat need not know it.
        self._keep = keep

    async def run_if_due(self, *, now: datetime | None = None) -> bool:
        """Back up once for today if today's backup is not already there.

        Returns whether a backup was made. The date is the guard, so this is
        safe to call every beat and safe across a restart: the backup files
        themselves are the record of having run, the same shape the daily
        summary uses its outbox row for. Runs the copy off the event loop —
        `sqlite3.backup` is blocking, and the loop is reading the gateway.
        """
        if self._keep <= 0:
            return False
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
        if self._dest(self._sources[0], stamp).exists():
            return False
        await asyncio.to_thread(self._backup_all, stamp)
        return True

    def _backup_all(self, stamp: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        made = []
        for src in self._sources:
            if not src.exists():
                continue
            dest = _dest(self._dir, src, stamp)
            _copy(src, dest)
            made.append(dest.name)
        self._prune()
        log.info("backed up %s to %s (keeping %d days)", ", ".join(made), self._dir, self._keep)

    def _dest(self, src: Path, stamp: str) -> Path:
        return _dest(self._dir, src, stamp)

    def _prune(self) -> None:
        """Keep the newest `keep` days; delete the files of older ones.

        A day, not a file: the two files of one backup are kept or dropped
        together, so a restore always has both halves of the state it needs.
        """
        by_day: dict[str, list[Path]] = {}
        for src in self._sources:
            for path in self._dir.glob(f"{src.stem}.*{src.suffix}"):
                stamp = _stamp_of(path, src)
                if stamp is not None:
                    by_day.setdefault(stamp, []).append(path)
        for stamp in sorted(by_day, reverse=True)[self._keep:]:
            for path in by_day[stamp]:
                path.unlink(missing_ok=True)


def _copy(src: Path, dest: Path) -> None:
    """One online, consistent snapshot of `src` at `dest`, overwriting it."""
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _stamp_of(path: Path, src: Path) -> str | None:
    """The `YYYYMMDD` between `<stem>.` and `<suffix>`, or `None` if the name
    is not one of ours — so an unrelated file in the directory is left alone."""
    prefix, suffix = f"{src.stem}.", src.suffix
    name = path.name
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    stamp = name[len(prefix):len(name) - len(suffix)] if suffix else name[len(prefix):]
    return stamp if stamp.isdigit() else None


def restore(stamp: str, *, sources: list[str], backup_dir: str) -> list[str]:
    """Copy one day's backup back over the live files. Returns what was
    restored. The agent must not be running — this overwrites the files it
    reads and writes. Missing halves are refused loudly rather than restoring a
    task without its workflow, or the other way round.

    **The WAL sidecars are cleared.** The case this exists for is a crash,
    which leaves a stale `<db>-wal`/`-shm` beside the live path; SQLite would
    replay that WAL against the just-restored file on next open and reintroduce
    exactly the state the restore was meant to discard. A backup is a standalone
    snapshot, so a restore is a plain file copy over the target — which does
    *not* touch the sidecars — followed by deleting them.
    """
    directory = Path(backup_dir)
    targets = [Path(s) for s in sources]
    missing = [
        str(_dest(directory, target, stamp))
        for target in targets
        if not _dest(directory, target, stamp).exists()
    ]
    if missing:
        raise FileNotFoundError(
            f"no backup for {stamp}: {', '.join(missing)}. Both files of a day "
            "restore together; refusing a partial restore."
        )
    for target in targets:
        shutil.copyfile(_dest(directory, target, stamp), target)
        for suffix in _SIDECARS:
            target.with_name(target.name + suffix).unlink(missing_ok=True)
    return [str(t) for t in targets]


def _main() -> None:
    """`uv run python -m friday.kernel.ops.backup restore <YYYYMMDD>` — the
    documented restore command (§12.1). Reads the file paths from `config.yaml`,
    so it names the same two databases the agent does. The daily backup itself
    rides the heartbeat; there is no manual backup verb."""
    import sys

    from friday.kernel.config import load_config

    args = sys.argv[1:]
    if not (len(args) == 2 and args[0] == "restore"):
        raise SystemExit(
            "usage: python -m friday.kernel.ops.backup restore <YYYYMMDD>\n"
            "restore overwrites the live databases; stop the agent first."
        )
    config = load_config()
    done = restore(args[1], sources=databases(config.database_path), backup_dir=config.backup_dir)
    print(f"restored {', '.join(done)} from {args[1]}")


if __name__ == "__main__":
    _main()
