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
    """Scrubs a record's message, its arguments, and its traceback.

    Attached to the root logger, so it covers libraries too — which is where
    the leak would come from, not from our own code.

    **The exception is the point, not an afterthought.** This module exists
    for "a provider error quoting the Authorization line, landing in a log
    file", and for a while it did not cover that: it scrubbed `record.msg` and
    string arguments, and a library logging `logger.error("%s failed: %s",
    name, exc, exc_info=exc)` slipped past both — `exc` is an object, so
    `_scrub_one` returned it untouched and the formatter called `str()` on it
    afterwards, and `exc_info` was never looked at at all. The SDK's own tool
    error path is exactly that shape (`agents/tool.py`), logged at ERROR with
    the raw arguments beside it.

    `exc_text` is where a formatter caches the rendered traceback, and it uses
    it if it is already set — so filling it in with a scrubbed rendering is
    how a filter reaches something otherwise formatted after every filter has
    run.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            record.args = _scrub_args(record.args)
        if record.exc_info and record.exc_text is None:
            record.exc_text = scrubbed_traceback(*record.exc_info)
        return True


def _scrub_args(args):
    if isinstance(args, dict):
        return {k: _scrub_one(v) for k, v in args.items()}
    return tuple(_scrub_one(a) for a in args)


def _scrub_one(value):
    """A string, or an exception rendered as one.

    An exception is not a string and is formatted like one: `%s` calls `str()`
    on it long after this filter has run, so leaving the object in place left
    whatever it quotes unscrubbed. Rendering it here is what puts it inside
    the only scrub there is. Every other type is left alone — a `%d` handed a
    string is a formatting error, and this must not invent one.
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, BaseException):
        return scrub(str(value))
    return value


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
