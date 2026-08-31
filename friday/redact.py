"""Keeping credentials out of anything that is written down.

The Discord user token is unscoped access to the whole account, with no
revocation short of a password change. The realistic leak is not someone
printing it on purpose — it is an unhandled exception carrying a request
header, or a provider error quoting the Authorization line, landing in a log
file or in a task's stored parameters.

So this runs on the way *out*: on every log record, and on anything the model
layer stores. Redacting at the point of writing is the only place it can be
made unconditional.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import traceback

__all__ = ["Redacting", "install_excepthook", "scrub", "scrubbed_traceback"]

#: Deliberately broad. A false positive costs a few unreadable characters in a
#: log line; a false negative is an account.
_SECRETS = re.compile(
    r"""(
        \bsk-[A-Za-z0-9_-]{16,}                  # OpenAI-style keys
      | \bBearer\s+[A-Za-z0-9._~+/=-]{16,}       # Authorization headers
      | \beyJ[A-Za-z0-9._-]{20,}                 # JWTs, and Discord tokens
      | \b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}  # Discord bot
    )""",
    re.VERBOSE,
)


def scrub(text: str) -> str:
    return _SECRETS.sub("[REDACTED]", text)


class Redacting(logging.Filter):
    """Scrubs a record's message and its arguments.

    Attached to the root logger, so it covers libraries too — which is where
    the leak would come from, not from our own code.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            record.args = _scrub_args(record.args)
        return True


def _scrub_args(args):
    if isinstance(args, dict):
        return {k: _scrub_one(v) for k, v in args.items()}
    return tuple(_scrub_one(a) for a in args)


def _scrub_one(value):
    return scrub(value) if isinstance(value, str) else value


def scrubbed_traceback(exc_type, exc, tb) -> str:
    return scrub("".join(traceback.format_exception(exc_type, exc, tb)))


def install_excepthook() -> None:
    """Scrub what a crash prints.

    The logging filter cannot reach this. Python's default hook writes the
    traceback straight to stderr, and a traceback carries every argument in
    every frame — which is exactly where a token turns up: a library raising on
    a request that had an Authorization header on it.

    Threads get their own hook, because they have their own: `aiosqlite` runs
    its connection on a worker thread, and an exception there goes through
    `threading.excepthook` and nowhere near `sys.excepthook`.
    """

    def on_crash(exc_type, exc, tb) -> None:
        print(scrubbed_traceback(exc_type, exc, tb), file=sys.stderr, end="")

    sys.excepthook = on_crash
    threading.excepthook = lambda args: on_crash(
        args.exc_type, args.exc_value, args.exc_traceback
    )
