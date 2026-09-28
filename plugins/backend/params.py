"""`backend.trace_problem`'s parameters — the plugin's own, against the sdk.

Moved out of the core models (now `friday.kernel.domain.models`) in ticket 14: a task type's parameter
dataclass ships with the plugin that owns it, and it declares its validation
rules against `friday.sdk.validation` (the DSL re-exported there) so the plugin
imports `friday.sdk` alone. The class docstring below reaches a model — triage's
`classify` tool reads it as this type's description — so it stays prose about
what the type is, one line each, the same place its fields are defined.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from friday.sdk.validation import InSet, Matches, OneOf

__all__ = ["ApiIssueParams"]


@dataclass(frozen=True, slots=True)
class ApiIssueParams:
    """Something this team's systems did, or did not do, that somebody wants
    looked at: an integration failing, a request, log, curl or response with
    an error code to check, a symptom with no name yet ("I bought the plan
    at 15:00 and half an hour later the coins are still not there"), or a
    question about what an endpoint is for, which one fits their case, and
    how its rules behave — an API is business logic reachable over HTTP, so
    a question about that logic belongs here rather than in
    answer_question."""

    #: Every field has a default, because nothing fills them in at
    #: construction time any more. Triage classifies and stops; the task is
    #: opened with no parameters at all, and the extractor fills them from
    #: what the reporter actually wrote.
    #:
    #: `doc` is the field's meaning, *for the extraction model*. It renders
    #: into the extractor's prompt, so it is written to the model: what to
    #: look for, what shape it has, and that null beats a guess.
    #:
    #: `ask` is how to ask a *person* about it — the same field, a different
    #: reader, so a different string. `doc` addresses a model about
    #: recognising a value ("null if none is named"); `ask` is the phrase the
    #: responder writes a question from, and what a reporter eventually reads
    #: is the responder's wording of it, never this text verbatim.
    summary: str = field(
        default="",
        metadata={
            "doc": "One line saying what is wrong, in Vietnamese, in your own "
            "words. The only field you write rather than copy."
        },
    )
    environment: str | None = field(
        default=None,
        metadata={
            # `staging` was in this enum and in this sentence until board
            # `read-it-the-way-the-operator-does`, ticket 01: no project this
            # room serves has a staging environment, so the only thing the
            # word could do was let a reporter's guess validate cleanly and
            # send `Resolve` looking for logs of somewhere that does not
            # exist. `external` is deliberately absent too — it is what the
            # `environment` rows *conclude* about a domain (see
            # `plugins/backend/graph/resolve.py`), never something a reporter
            # names about themselves.
            "doc": "Which environment they named: production or dev. 'prod' "
            "is production. null if none is named.",
            "ask": "which environment you're on",
        },
    )
    response: str | None = field(
        default=None,
        metadata={
            # **Where a correlationId actually comes from** (ticket 01). The
            # operator never receives one from a reporter; they receive the
            # response the reporter pasted and read it out of that. Asking a
            # reporter for "the correlationId" asks them to do a lookup they
            # do not know how to do, and the three `ask_for_details` messages
            # this system has ever sent all had to teach it inline — which is
            # the responder adding content nobody approved
            # (`tests/test_responder_check.py`).
            #
            # An artifact id, like `curl` and for ticket 18's reason: a
            # response body is long, and a model copying it out by hand gets
            # a character wrong.
            "doc": "The id of the artifact holding the response they pasted "
            "— the `ab12cd34` in `[artifact ab12cd34: …]`, on its own, "
            "nothing else. Do not copy the response itself: it is put back "
            "for you. If they typed it inline with no artifact around it, "
            "give it as they wrote it. null if they pasted no response.",
            "ask": "the response you got back",
        },
    )
    endpoint: str | None = field(
        default=None,
        metadata={
            "doc": "The endpoint they called, as they named it — a path like "
            "`/v1/onboarding/completed`, or the name they used for it "
            "('login API'). null if they named none.",
            "ask": "which endpoint you called",
        },
    )
    identifier: str | None = field(
        default=None,
        metadata={
            "doc": "One id the failing request carried, copied exactly, "
            "whatever kind they gave: deviceId, userId, email, orderId. It "
            "is matched against log lines by machine, so copy it as written "
            "and do not reformat it. null if they gave none.",
            "ask": "the deviceId, userId, email or order id you used",
        },
    )
    #: **Read out of `response`, never asked for** — which is why it carries a
    #: `doc` and no `ask`, and so is not in `askable_fields`. The value is
    #: short enough for a model to copy correctly, which is why it is lifted
    #: into a field of its own rather than left inside the span: the log
    #: sources search for one plain substring, and a whole response body is
    #: not one.
    correlation_id: str | None = field(
        default=None,
        metadata={
            "doc": "The correlation id, trace id, request id or x-request-id "
            "in what they wrote — usually inside the response they pasted — "
            "copied exactly: it is matched by machine. Usually shaped like a "
            "uuid. null if absent.",
        },
    )
    curl: str | None = field(
        default=None,
        metadata={
            # Board `read-it-the-way-the-operator-does`, ticket 18. This used
            # to say "verbatim with its line breaks — somebody will paste it
            # into a terminal", and that is still what the field has to hold;
            # it is no longer what the model is asked to produce. Task 6
            # stored 676 characters of a 678-character Bearer token because
            # copying it out by hand is a thing a model does imperfectly and
            # a thing code does not do at all.
            "doc": "The id of the artifact holding the request they pasted — "
            "the `ab12cd34` in `[artifact ab12cd34: …]`, on its own, nothing "
            "else. Do not copy the request itself: it is put back for you. "
            "If they typed it inline with no artifact around it, give the "
            "command as they wrote it. null if there is no request at all.",
            "ask": "the curl you used",
        },
    )

    #: Validate catches what the LLM extractor got wrong. `environment` has to
    #: be one of the two environments we actually serve; `correlation_id`
    #: has to look like a uuid for Loki's query_range filter to find it.
    #:
    #: `_traceable` is this type's override of the general required-ness rule,
    #: which reads its answer off the annotations: every field below is
    #: `str | None`, so none of them is individually required — and yet a
    #: report that names no request at all cannot be investigated. That is not
    #: something a type can say, which is what `OneOf` is for.
    #:
    #: **The alternatives are the curl, or the endpoint plus one id** (D2,
    #: and ticket 01's correction of what this rule used to say). The endpoint
    #: alone is not enough: measured on the production case, 2026-09-21,
    #: matching on the path alone returns every *other* caller's successful
    #: request to it — 18 dossier lines where the id gives 8 — so the id is
    #: what narrows it to this reporter.
    _RULES = {
        "environment": InSet(frozenset({"production", "dev"})),
        "correlation_id": Matches(
            r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
            name="uuid",
            # The phrase is on the rule because the field has none: a
            # malformed correlationId is answered by the response it should
            # have been read out of, not by asking the reporter to go and
            # find an id.
            ask="the response you got back",
        ),
        "_traceable": OneOf(
            fields=("curl", ("endpoint", "identifier")),
            ask="the curl you used, or which endpoint you called plus one id "
            "it carried",
        ),
    }
