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

from friday.redact import scrub, scrubbed_traceback

SECRET = "sk-abcdefghijklmnopqrstuvwxyz01"
ROOT = Path(__file__).resolve().parent.parent


def test_the_shapes_a_credential_comes_in():
    assert SECRET not in scrub(f"401 for Bearer {SECRET}")
    assert "REDACTED" in scrub(f"Authorization: Bearer {SECRET}")
    assert "REDACTED" in scrub("token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdef")


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
        "from friday.redact import install_excepthook\n"
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
        "from friday.redact import install_excepthook\n"
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
    )

    assert SECRET not in done.stderr
