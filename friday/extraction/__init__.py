"""A field extractor per workflow.

Each workflow that needs fields from text declares one. The extractor is a
`Harness` whose schema is the workflow's `Params` type, so the same place that
describes a field describes the rule for filling it. The model may
hallucinate; that is the cost of LLM extraction, and ticket 30's validate
engine catches what it gets wrong.

Registered at startup by `register_extractors`, looked up by task type, and
called by `prepare()`, which is node 0 of every graph. Adding one is an entry
in `EXTRACTS` and a block in `config.yaml`, not a change to the composition
root.

An extractor also carries `ask_clarification` (ticket 05): the model has just
read everything the reporter said, and may know something is missing that no
structural rule catches. It names *which* of its own fields, and why — never
words, so the tool cannot be argued into phrasing that bypasses the operator's
voice — and `friday.dag.prepare.prepare` turns that into an `Ask` the Responder
writes. Code stays the floor regardless: a field the structural rules reject
is challenged with the code template whether or not the model asked about it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, fields
from typing import Any, Literal

from friday.agent.harness import Harness
from friday.extraction.clarify import Clarify, FieldsCapture
from friday.tools.ask_for_fields import ask_for_fields_tool
from friday.domain.models import MODEL_AUTHORED, PARAMS, Params
from friday.extraction.prompt import build_input, build_instructions

__all__ = [
    "Clarify",
    "EXTRACTS",
    "Extractor",
    "build_extractor",
    "extract",
    "register",
    "register_extractors",
    "registered",
]

log = logging.getLogger(__name__)

#: Task type -> the extractor that fills its parameters. Written by
#: `register()`, read by `extract()` from inside `prepare()`.
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

    async def run(
        self, text: str, *, task_id: int | None = None, node: str | None = None
    ) -> tuple[Params | None, Clarify | None]:
        """Ask the model to fill the fields. `(None, None)` if the call failed.

        The harness already swallows exceptions into `last_error`, so a
        `None` params here means "the model could not answer" — the workflow
        falls back to the structural check, which is the right behaviour:
        nothing to hallucinate means nothing to validate. Whether the model
        also called `ask_clarification` is independent of that — one extra
        turn covers the tool call landing before or after the field text.
        """
        capture = FieldsCapture()
        result = await self._harness.run(
            build_input(text, self._params_cls),
            context=capture,
            extra_turns=1,
            task_id=task_id,
            node=node,
        )
        if result is None:
            log.warning("extractor %s returned no result", self.name)
            return None, None
        read = _parse(result.final_output or "")
        known = set(self._params_cls.__dataclass_fields__)
        unknown = sorted(set(read) - known)
        if unknown:
            # Dropped, not fatal. One line the model decorated or invented used
            # to raise on the constructor and discard everything — including
            # the fields it had read correctly, which is the opposite of what
            # a best-effort step should do when it half succeeds.
            log.info(
                "extractor %s: ignoring %s", self.name, ", ".join(map(repr, unknown))
            )
        try:
            filled = _hygiene(
                self._params_cls(**{k: v for k, v in read.items() if k in known})
            )
        except (TypeError, ValueError) as exc:
            log.warning(
                "extractor %s output did not match schema: %s",
                self.name,
                exc,
            )
            return None, capture.clarify
        return filled, capture.clarify


def build_extractor(
    *, params_cls: type[Params], harness: Harness, name: str
) -> Extractor:
    """Wire a Harness to a Params class under a name.

    Use this when the extractor is built programmatically (e.g. from
    `config.yaml`). `register(...)` is the registration
    shortcut.

    The `task_type` for which this extractor is registered must match
    `params_cls`: registering an `api_issue` extractor with
    `params_cls=DocQuestionParams` would silently produce the wrong type at
    runtime. The check is enforced at registration, not at extraction, so a
    misconfigured system fails to start rather than producing a wrong answer.
    """
    return Extractor(harness=harness, params_cls=params_cls, name=name)


def registered() -> dict[str, Extractor]:
    """Snapshot of the registry, for inspection (e.g. by tests)."""
    return dict(_EXTRACTORS)


async def extract(
    task_type: str,
    text: str,
    *,
    task_id: int | None = None,
    node: str | None = None,
) -> tuple[Params | None, Clarify | None]:
    """Run the extractor registered for `task_type` over `text`.

    Returns the filled Params, or None if no extractor is registered or the
    extractor failed. None is the right answer for "skip this step" — the
    caller treats it as "no extra information found, do not validate the
    hallucinated fields". The `Clarify`, if any, is independent of whether
    the params came back — the model may have called the tool and still
    written nothing usable, or the reverse.
    """
    ext = _EXTRACTORS.get(task_type)
    if ext is None:
        return None, None
    return await ext.run(text, task_id=task_id, node=node)





def register(
    task_type: str,
    params_cls: type[Params],
    config: "AgentConfig",  # type: ignore[name-defined]  # noqa: F821
    *,
    record=None,
) -> None:
    """Register one task type's extractor from configuration.

    One function for every task type rather than one per type: they differ
    only in which `Params` they fill, and three copies of the same twenty
    lines is three places for them to drift.

    The params class is checked against `PARAMS` rather than trusted. An
    extractor registered against the wrong schema produces the wrong `Params`
    at runtime, in the middle of a task, where the only symptom is fields that
    never fill in. Refusing at startup costs a restart.
    """
    from friday.agent.harness import Harness

    if PARAMS.get(task_type) is not params_cls:
        raise ValueError(
            f"cannot register the {task_type} extractor: PARAMS[{task_type!r}] "
            f"is not {params_cls.__name__} (got {PARAMS.get(task_type)})"
        )

    _EXTRACTORS[task_type] = build_extractor(
        params_cls=params_cls,
        harness=Harness(
            config=config,
            instructions=build_instructions(),
            tools=[ask_for_fields_tool(params_cls)],
            context_type=FieldsCapture,
            record=record,
        ),
        name=f"{task_type}_extractor",
    )


def _hygiene(params: Params) -> Params:
    """Trim every value, and treat absent-looking text as absent.

    Moved here from triage when triage stopped producing values. The live
    provider taught us the lesson it protects against: the model writes the
    *string* `"null"` often enough that an unnormalised value is mistaken for a
    real one — and a workflow that believes it has a correlationId will never
    ask for the one it needs.
    """
    from friday.text.param_hygiene import clean

    return type(params)(
        **{
            f.name: clean(value) if isinstance(value := getattr(params, f.name), str)
            else value
            for f in fields(params)
        }
    )


def _parse(output: str) -> dict[str, Any]:
    """Coerce a model output to a kwargs dict for the Params class.

    Models produce prose like `environment: production` or JSON. We accept
    either, and the caller keeps only the keys its schema knows — this returns
    what it read, not what is valid.

    Keys are stripped of the decoration a model puts around them. Asked for
    JSON it frequently answers with a Markdown list, and `- environment:
    production` was read as a field literally called `- environment`. One such
    line raised on the dataclass constructor and lost the whole extraction,
    including the correlationId two lines above it that had been read
    correctly.
    """
    data = _json_object(output)
    if data is not None:
        return data
    # Fallback: `key: value` lines. Crude but works for the small schemas we
    # deal with; ticket 31 is a starting point, not a finished format.
    result: dict[str, Any] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = _undecorate(key)
        value = value.strip().strip("\"'`")
        if not key or value.lower() in ("null", "none", ""):
            continue
        result[key] = value
    return result


def _json_object(output: str) -> dict[str, Any] | None:
    """The JSON object in the output, wherever it is. None if there is none.

    Not `startswith("{")`. A model asked for JSON only routinely answers with a
    sentence first, a fenced block, or its own reasoning — and the check used
    to fail on all three, dropping perfectly good JSON into the line-by-line
    fallback below, which then read `"environment": null,` as the *string*
    `"null,"`. A truthy string in `correlation_id` passes the "is there
    anything to trace on" gate and fails the uuid rule, so the reporter is
    asked to resend a correlationId they already sent correctly. Observed, on
    the real provider, exactly that way.

    Braces are matched rather than searched for, because the last `}` in the
    output may belong to prose after the object.
    """
    import json

    start = output.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(output)):
            if output[i] == "{":
                depth += 1
            elif output[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(output[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    return data if isinstance(data, dict) else None
        start = output.find("{", start + 1)
    return None


#: Bullets, numbering and emphasis a model puts around a field name when it
#: answers in Markdown instead of the JSON it was asked for.
_DECORATION = re.compile(r"^[\s>*+-]*(?:\d+[.)]\s*)?[`*_\"']*|[`*_\"']*$")


def _undecorate(key: str) -> str:
    return _DECORATION.sub("", key.strip()).strip()


#: Task type -> the `Params` its extractor fills. Every classifiable type is
#: here, because triage no longer fills anything: a type whose extractor is
#: not configured opens tasks with empty parameters, and the reporter is asked
#: for what they already said.
EXTRACTS = {
    "api_issue": "ApiIssueParams",
    "access_request": "AccessRequestParams",
    "doc_question": "DocQuestionParams",
}


def register_extractors(config: "Config", *, record=None) -> None:  # type: ignore[name-defined]  # noqa: F821
    """Wire every extractor the configuration declares.

    Composition root calls this once at startup and learns nothing about any
    individual extractor. Adding one is a line in `EXTRACTS` and a block in
    `config.yaml`, not a change there.

    A missing block is a warning rather than a failure, because a broken
    install that starts and says what is wrong beats one that will not start.
    It is a loud warning: nothing else fills those fields.
    """
    from friday.domain import models

    for task_type, params_name in EXTRACTS.items():
        block = f"extractor_{task_type}"
        agent_config = config.agents.get(block)
        if agent_config is None:
            log.warning(
                "no %s in config.yaml — %s tasks will open with no parameters "
                "and the reporter will be asked for what they already said",
                block,
                task_type,
            )
            continue
        register(task_type, getattr(models, params_name), agent_config, record=record)
