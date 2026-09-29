"""Turn cases on disk: one markdown file per case, one folder per label.

    evals/datasets/<eval>/
      <label>/NNN-slug.md        a case; its folder is its expected label
      _messages/<name>.md        a long message (a ticket, a curl) a case
                                 names by path — not a case itself

A case file is YAML frontmatter and a body:

    ---
    expected_task: backend.trace_problem
    turn:                       # optional: the messages of one turn, in order
      - text: "Cứu @Lee ơi"
      - file: _messages/pod-submit-failed.md
      - text: "check xem nguyên nhân là gì nhé"
        own: true               # sent by the operator's own account
    ---
    (no turn: the body is the one message, pasted as is)

Written by hand, so a malformed file is refused with its
path and the reason, never skipped: a case the operator believes is scored and
silently is not is worse than a run that says which file is wrong.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

from friday.sdk.eval import EvalCase

__all__ = ["CaseError", "Turn", "load_turn_cases", "write_turn_case"]

#: A turn: `(text, own)` per message, in order.
Turn = tuple[tuple[str, bool], ...]

#: The one key a case file needs; `turn` is the only other allowed.
EXPECTED = "expected_task"
_KEYS = {EXPECTED, "turn"}
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class CaseError(ValueError):
    """A case file that cannot be read as a case, named with its path."""


def load_turn_cases(root: Path) -> list[EvalCase]:
    """Every case under `root`, sorted by path so two runs compare. Folders
    starting with `_` hold messages, not cases."""
    cases = []
    for path in sorted(root.glob("*/*.md")):
        if path.parent.name.startswith("_"):
            continue
        cases.append(_case(root, path))
    return cases


def _case(root: Path, path: Path) -> EvalCase:
    where = path.relative_to(root).as_posix()
    # `utf-8-sig`: several editors write a byte-order mark, and with it the
    # file does not start with `---` (the skill loader learned this first).
    match = _FRONTMATTER.match(path.read_text(encoding="utf-8-sig"))
    if match is None:
        raise CaseError(f"{where}: no `---` frontmatter at the top")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise CaseError(f"{where}: frontmatter is not valid YAML: {exc}") from None
    if not isinstance(meta, dict) or set(meta) - _KEYS:
        raise CaseError(f"{where}: frontmatter may hold only {sorted(_KEYS)}")
    label = path.parent.name
    if meta.get(EXPECTED) != label:
        raise CaseError(
            f"{where}: `{EXPECTED}` is {meta.get(EXPECTED)!r}, but the case sits "
            f"in the {label!r} folder — the two must agree"
        )
    body = match.group(2).strip()
    if "turn" in meta:
        if body:
            raise CaseError(
                f"{where}: a case with a `turn` has no body — put every message in the turn"
            )
        turn = tuple(_message(root, where, item) for item in meta["turn"] or ())
    elif body:
        turn = ((body, False),)
    else:
        raise CaseError(f"{where}: no message — write it in the body, or give a `turn`")
    if not turn:
        raise CaseError(f"{where}: `turn` is empty")
    return EvalCase(name=where.removesuffix(".md"), inputs=turn, expected=label)


def _message(root: Path, where: str, item: object) -> tuple[str, bool]:
    if (
        not isinstance(item, dict)
        or set(item) - {"text", "file", "own"}
        or (("text" in item) == ("file" in item))
    ):
        raise CaseError(
            f"{where}: each turn message is `text:` or `file:` (and optional `own:`), got {item!r}"
        )
    if "file" in item:
        file = (root / str(item["file"])).resolve()
        if not file.is_relative_to(root.resolve()) or not file.is_file():
            raise CaseError(
                f"{where}: `file: {item['file']}` is not a file under {root.name}/"
            )
        text = file.read_text(encoding="utf-8").strip()
        if not text:
            raise CaseError(
                f"{where}: `file: {item['file']}` is empty — paste the message into it"
            )
    else:
        text = str(item["text"])
        if not text.strip():
            raise CaseError(f"{where}: a turn message has empty `text`")
    return text, bool(item.get("own", False))


def write_turn_case(root: Path, label: str, text: str) -> Path:
    """Add a one-message case under `root/label/`, numbered after the last
    one there. Never overwrites: a hand-written case is the operator's."""
    folder = root / label
    folder.mkdir(parents=True, exist_ok=True)
    numbers = [int(p.name[:3]) for p in folder.glob("[0-9][0-9][0-9]-*.md")]
    path = folder / f"{max(numbers, default=0) + 1:03d}-{_slug(text)}.md"
    path.write_text(
        f"---\n{EXPECTED}: {label}\n---\n\n{text.strip()}\n", encoding="utf-8"
    )
    return path


def _slug(text: str) -> str:
    """A few ASCII words from the message, for a file name a person can scan."""
    folded = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    ascii_text = folded.encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", ascii_text)[:6]
    return "-".join(words) or "case"
