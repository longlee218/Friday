"""The prompt texts, as files, loaded once.

`prompts/` at the repository root holds every instruction block that used to be
a string constant in a `.py` file — one file per prompt, editable as prose,
diffable as prose, and owned by whoever maintains the wording rather than
whoever maintains the code around it. Editing one is a restart, not a rebuild;
the directory is mounted read-only in the container like `PERSONA.md`.

Two things distinguish these from the persona and the skills:

- **They are required.** An agent with empty instructions is broken, not
  degraded, so a missing file fails at import — at startup, with the file
  named — never mid-task.
- **They carry contracts.** `analyze_stack.md` names the JSON keys its parser
  expects; `responder.md` names sections and a tool. The words are prose; the
  names in them are load-bearing. `prompts/README.md` says so to whoever edits,
  because the files themselves cannot carry a comment — every byte in them
  ships to a model verbatim.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["prompt"]

#: Resolved from this file, not the working directory, so imports work from
#: anywhere — tests, scripts, the container. Holds while the app runs from the
#: repository, which is the only way it runs.
_DIR = Path(__file__).resolve().parents[2] / "prompts"

_cache: dict[str, str] = {}


def prompt(name: str) -> str:
    """The text of `prompts/<name>.md`, stripped, cached for the process."""
    if name not in _cache:
        path = _DIR / f"{name}.md"
        try:
            _cache[name] = path.read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            raise RuntimeError(
                f"prompt {name!r} is missing: {path} could not be read ({exc}). "
                f"Every agent's instructions live under prompts/ — an agent "
                f"without one is broken, so this refuses to start."
            ) from exc
    return _cache[name]


def loaded() -> frozenset[str]:
    """Which prompts this process has read — for the test that catches an
    orphaned file nobody loads."""
    return frozenset(_cache)
