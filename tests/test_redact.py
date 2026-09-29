"""Keeping credentials out of what is written down.

The Discord user token is unscoped access to the whole account, revocable only
by changing the password. The realistic leak is not someone printing it: it is
a library raising an exception that carries a request header.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from friday.kernel.ops.redact import scrub, scrubbed_traceback

SECRET = "sk-abcdefghijklmnopqrstuvwxyz01"
ROOT = Path(__file__).resolve().parent.parent


def test_the_shapes_a_credential_comes_in():
    assert SECRET not in scrub(f"401 for Bearer {SECRET}")
    assert "REDACTED" in scrub(f"Authorization: Bearer {SECRET}")
    assert "REDACTED" in scrub("token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdef")


def test_a_declared_secret_is_redacted_by_value():
    """A configured key that matches no known pattern (§12, §3.2): the
    composition root registers the exact values it holds, and `scrub` redacts
    them wherever they turn up — a shape-based list alone would miss it."""
    from friday.sdk.redact import (
        clear_secret_values,
        matches_secret_value,
        register_secret_values,
    )

    odd = "not-shaped-like-anything-XYZ42"
    try:
        register_secret_values([odd])
        assert scrub(f"connecting with {odd} now") == "connecting with [REDACTED] now"
        assert matches_secret_value(f"...{odd}...")
    finally:
        clear_secret_values()
    # Once cleared, nothing knows about it again — a test must not leak a value
    # into the next.
    assert scrub(odd) == odd


def test_blank_and_single_characters_are_not_treated_as_secrets():
    """An empty or one-character 'secret' would blank out ordinary prose. The
    registry drops them rather than turning every 'a' into [REDACTED]."""
    from friday.sdk.redact import clear_secret_values, register_secret_values

    try:
        register_secret_values(["", " ", "a", "ok"])
        assert scrub("a cat sat") == "a cat sat"
    finally:
        clear_secret_values()


def test_a_correlation_id_is_not_a_credential():
    """It is a required task parameter. Redacting one would break the workflow
    built to ask for it."""
    text = "API lỗi rồi, correlationId abc-123-def, môi trường production"

    assert scrub(text) == text


def test_a_traceback_is_scrubbed_whole():
    try:
        raise RuntimeError(f"401 for Bearer {SECRET}")
    except RuntimeError:
        rendered = scrubbed_traceback(*sys.exc_info())

    assert SECRET not in rendered
    assert "RuntimeError" in rendered  # still a usable traceback


def test_a_crash_does_not_print_the_token(tmp_path):
    """Python's default excepthook writes straight to stderr and never passes
    through a logging filter, so the filter alone does not cover this."""
    script = tmp_path / "crash.py"
    script.write_text(
        "from friday.kernel.ops.redact import install_excepthook\n"
        "install_excepthook()\n"
        f"raise RuntimeError('crashed with Bearer {SECRET}')\n"
    )

    done = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        # The script lives in a temp dir, and python puts *its* directory on
        # the path — not the working directory.
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=False,
    )

    assert SECRET not in done.stderr
    assert "REDACTED" in done.stderr
    assert done.returncode != 0  # still a crash


def test_a_crash_in_a_thread_is_scrubbed_too(tmp_path):
    """aiosqlite runs its connection on a worker thread. A thread's exception
    goes through a different hook."""
    script = tmp_path / "thread_crash.py"
    script.write_text(
        "import threading\n"
        "from friday.kernel.ops.redact import install_excepthook\n"
        "install_excepthook()\n"
        f"t = threading.Thread(target=lambda: 1 / 0 if False else (_ for _ in ()).throw(RuntimeError('Bearer {SECRET}')))\n"
        "t.start(); t.join()\n"
    )

    done = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        # The script lives in a temp dir, and python puts *its* directory on
        # the path — not the working directory.
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=False,
    )

    assert SECRET not in done.stderr


def _rendered(emit) -> str:
    """One log record through `Redacting` and a real formatter.

    Through the formatter on purpose: the filter runs first and the message,
    the arguments and the traceback are rendered afterwards, so a test that
    inspects the record instead of the output cannot see what actually reaches
    the file.
    """
    import io
    import logging

    from friday.kernel.ops.redact import Redacting

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("redact-probe")
    log.handlers = [handler]
    log.filters = [Redacting()]
    log.setLevel(logging.DEBUG)
    log.propagate = False

    emit(log)
    return stream.getvalue()


def test_an_exception_passed_as_an_argument_is_scrubbed():
    """The shape this module was written for, and the one it used to miss.

    `logger.error("%s failed: %s", name, exc)` — an exception is an object, so
    it was returned untouched and `str()` was called on it by the formatter,
    after every filter had run. The SDK's tool error path logs exactly this,
    at ERROR, with the raw tool arguments beside it.
    """
    TOKEN = "Bearer abcdefghijklmnop0123456789ABCDEF"

    def emit(log):
        try:
            raise RuntimeError(f"provider rejected: {TOKEN}")
        except RuntimeError as exc:
            log.error("%s failed: %s", "classify", exc)

    said = _rendered(emit)
    assert TOKEN not in said, said
    assert "[REDACTED]" in said


def test_a_traceback_is_scrubbed():
    """`exc_info` is rendered by the formatter and never passes through a
    filter's hands, so the filter has to render it itself. `exc_text` is the
    slot a formatter checks first, which is what makes that possible."""
    TOKEN = "Bearer abcdefghijklmnop0123456789ABCDEF"

    def emit(log):
        try:
            raise RuntimeError(f"provider rejected: {TOKEN}")
        except RuntimeError as exc:
            log.error("classify failed", exc_info=exc)

    said = _rendered(emit)
    assert TOKEN not in said, said
    assert "RuntimeError" in said, "the traceback is still readable"
