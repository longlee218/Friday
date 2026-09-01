"""A field extractor per workflow.

Each workflow that needs fields from text declares one. The extractor is a
`Harness` whose schema is the workflow's `Params` type, so the same place that
describes a field describes the rule for filling it. The model may
hallucinate; that is the cost of LLM extraction, and ticket 30's validate
engine catches what it gets wrong.

Pattern mirrors `@planner(...)` in `friday/workflows/__init__.py` — registered
at import time, looked up by task type, called by `plan()`. No central registry
list: adding an extractor is adding a file, like adding a planner.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Callable

from friday.harness import Harness
from friday.models import Params

__all__ = ["Extractor", "build_extractor", "extract", "registered"]

log = logging.getLogger(__name__)

#: Task type -> extractor callable. Populated by `extractor(...)`; read by
#: `plan()` between validate and dispatch.
_EXTRACTORS: dict[str, "Extractor"] = {}


class Extractor:
    """A workflow's extraction agent, plus the instructions that drive it.

    Constructed by `build_extractor`; called by `extract`. The Harness is held
    but not eagerly run — a plan only invokes the extractor when the params
    actually have a relevant text source, and only after the validate engine
    has decided the params are worth filling in.
    """

    def __init__(self, harness: Harness, params_cls: type[Params], name: str) -> None:
        self._harness = harness
        self._params_cls = params_cls
        self.name = name

    async def run(self, text: str) -> Params | None:
        """Ask the model to fill the fields. None if the call failed.

        The harness already swallows exceptions into `last_error`, so a None
        here means "the model could not answer" — the workflow falls back to
        the structural check, which is the right behaviour: nothing to
        hallucinate means nothing to validate.
        """
        result = await self._harness.run(_prompt(text, self._params_cls))
        if result is None:
            log.warning("extractor %s returned no result", self.name)
            return None
        try:
            return self._params_cls(**_parse(result.final_output))
        except (TypeError, ValueError) as exc:
            log.warning(
                "extractor %s output did not match schema: %s",
                self.name,
                exc,
            )
            return None


def build_extractor(
    *, params_cls: type[Params], harness: Harness, name: str
) -> Extractor:
    """Wire a Harness to a Params class under a name.

    Use this when the extractor is built programmatically (e.g. from
    `config.yaml`). The `@extractor(...)` decorator is the registration
    shortcut.
    """
    return Extractor(harness=harness, params_cls=params_cls, name=name)


def extractor(task_type: str) -> Callable[[Extractor], Extractor]:
    """Register an Extractor for `task_type`. Used as a decorator:

        @extractor("api_issue")
        EXTRACTOR_API_ISSUE = build_extractor(...)

    Mirrors `@planner(...)` in `friday.workflows`.
    """

    def register(ext: Extractor) -> Extractor:
        if task_type in _EXTRACTORS:
            raise ValueError(
                f"extractor already registered for task type {task_type!r}"
            )
        _EXTRACTORS[task_type] = ext
        return ext

    return register


def registered() -> dict[str, Extractor]:
    """Snapshot of the registry, for inspection (e.g. by tests)."""
    return dict(_EXTRACTORS)


async def extract(task_type: str, text: str) -> Params | None:
    """Run the extractor registered for `task_type` over `text`.

    Returns the filled Params, or None if no extractor is registered or the
    extractor failed. None is the right answer for "skip this step" — the
    caller treats it as "no extra information found, do not validate the
    hallucinated fields".
    """
    ext = _EXTRACTORS.get(task_type)
    if ext is None:
        return None
    return await ext.run(text)


def _prompt(text: str, params_cls: type[Params]) -> str:
    """The extractor prompt: the schema first, the text second.

    Schema first so the model sees what to fill before it reads what to fill
    from. The fields' docstrings carry the rules — that is where
    "correlation_id looks like a uuid" lives, not in this prompt.
    """
    schema_lines = []
    for f in params_cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
        doc = (f.metadata or {}).get("doc", f.name.replace("_", " "))
        schema_lines.append(f"- {f.name}: {doc}")
    schema = "\n".join(schema_lines) or "(no fields)"
    return (
        "Fill every field below from the message. Pass null when the value "
        "is genuinely absent — never invent one. The model has already "
        "produced a first pass; your job is to look for what it missed.\n\n"
        f"Fields:\n{schema}\n\n"
        f"Message:\n{text}"
    )


def _parse(output: str) -> dict[str, Any]:
    """Coerce a model output to a kwargs dict for the Params class.

    Models produce prose like `environment: production` or JSON. We accept
    either; the dataclass constructor validates the types.
    """
    output = output.strip()
    if output.startswith("{"):
        import json

        try:
            data = json.loads(output)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    # Fallback: `key: value` lines. Crude but works for the small schemas we
    # deal with; ticket 31 is a starting point, not a finished format.
    result: dict[str, Any] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'`")
        if value.lower() in ("null", "none", ""):
            continue
        result[key] = value
    return result
