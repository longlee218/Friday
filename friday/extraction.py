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


def extractor(task_type: str, ext: Extractor) -> Extractor:
    """Register an Extractor for `task_type`.

        @extractor("api_issue", Extractor(...))

    Returns `ext` unchanged so it can sit at module top-level next to the
    instance it registers. `build_extractor(...)` followed by this decorator is
    the common shape:

        @extractor("api_issue", build_extractor(
            params_cls=ApiIssueParams, harness=harness, name="api_issue",
        ))
    """
    if task_type in _EXTRACTORS:
        raise ValueError(
            f"extractor already registered for task type {task_type!r}"
        )
    _EXTRACTORS[task_type] = ext
    return ext


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


#: Instructions shared by every extraction agent. The schema and the text vary
#: per call; the rule about not inventing is constant.
EXTRACTION_INSTRUCTIONS = """You fill structured fields from a chat message.

You are shown the field schema (names and what each is for) and the message
itself. For each field, copy the matching value verbatim from the message
when you can find one. Pass null when the value is genuinely absent — never
invent one. The triage step already produced a first pass; your job is to
look for what it missed, not to repeat what it got right.

Reply in JSON only, with the schema fields as keys."""


def register_api_issue_extractor(config: "AgentConfig") -> None:  # type: ignore[name-defined]  # noqa: F821
    """Register the `api_issue` extractor from configuration.

    Called by the composition root after config is loaded. Skipped silently if
    the configuration has no `extractor_api_issue` block — the workflow then
    runs without an extractor, which is the same behaviour as the no-
    extractor registration path. Registration is idempotent: re-running it
    replaces the previous registration.
    """
    from friday.harness import Harness
    from friday.models import ApiIssueParams

    api_ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=Harness(config=config, instructions=EXTRACTION_INSTRUCTIONS),
        name="api_issue_extractor",
    )
    _EXTRACTORS["api_issue"] = api_ext


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
