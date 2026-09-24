"""One agent process at a time.

Two `run_agent` processes against one database would run two outbox loops at
once — a reply sent twice, and every row raced over. The lock is an exclusive
`flock` on a file beside the database: the OS holds it for the life of the
process and releases it the instant the process exits, even on a crash, so
there is no stale lock file to clean up and no pid to go wrong.

`flock` is POSIX and advisory, and this rests on the local filesystem of the
operator's own machine (darwin/linux) — the crash-release guarantee is the
kernel's, and does not hold over NFS or on Windows, neither of which Friday
runs on.
"""

from __future__ import annotations

import fcntl
import pathlib
from typing import IO

__all__ = ["single_instance_lock"]


def single_instance_lock(database_path: str) -> IO[str]:
    """Take the exclusive lock beside the database, or refuse to start.

    Returns the held lock file. **Keep the reference for the life of the
    process** — closing it, or letting it be garbage-collected, releases the
    lock. Raises `SystemExit` with a clear line if another agent already holds
    it.
    """
    lock_path = pathlib.Path(f"{database_path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit(
            f"another agent is already running (it holds {lock_path}). Two would "
            "run two outbox loops against one database and could send a reply "
            "twice — stop the other process first."
        ) from None
    return handle
