"""PROTOTYPE — throwaway. Ticket 03 "What Intake gathers, and the shape the loop
returns" on board the-graph-becomes-a-loop. Shapes to react to, not to ship.
No behaviour, no validation — just the data shapes and the one open fork.

Reacts against the real shapes: plugins/devops/params.py (ApiIssueParams),
plugins/devops/graph/diagnose.py (Diagnosis), friday.sdk.sources.Placement.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

# ─────────────────────────────────────────────────────────────────────────────
# 1. What Intake gathers  (deterministic, NO LLM — charting Q8)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Placement:
    """Where this case lives. Env from the operator's domain table (never a
    model guess). See the FORK below for where `service` comes from now that
    extraction is gone."""
    env: Literal["production", "dev", "external"]
    service: str                 # canonical service name
    clone_path: str              # local checkout
    repo_path: str
    release_tag: str = ""        # what the cluster says it runs
    namespace: str = ""
    pod_selector: str = ""
    dbs: tuple[str, ...] = ()
    error_code_doc: str = ""     # path to the project's error-code table
    # REQUIRED by read_code: maps a compiled frame (dist/src/x.js) back to the
    # clone. Missing it = read_code can't resolve paths. (added — ticket 03)
    container_roots: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Hints:
    """Cheap, deterministic pulls from the reporter's raw text — regex / artifact
    ids only, NO model. The rich reading (which endpoint, what's wrong) is the
    LOOP's job, not Intake's. Mirrors the machine-matched fields of
    ApiIssueParams, minus anything that needed the extractor model."""
    correlation_id: str | None = None   # uuid regex over the text
    curl_artifact_id: str | None = None # `[artifact ab12cd34: …]`
    response_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntakeContext:
    """Intake's whole output. Runs fresh each pass; never checkpointed; its
    `placement_identity` is the staleness anchor (ticket 01)."""
    request_text: str            # the reporter's turn(s), passed straight to the loop
    # REQUIRED by read_log: the window is measured back from here
    # (since = reported_at - minutes_back). Missing it = the loop can't time its
    # reads. (added — ticket 03)
    reported_at: str             # ISO timestamp of the report
    placement: Placement
    hints: Hints
    # retrieved, token-bounded, deterministic (keyword / `when:` match — no model)
    memory: tuple[str, ...] = ()     # fact / constraint / decision / finding
    skills: tuple[str, ...] = ()     # `when:`-matched
    related_tasks: tuple[str, ...] = ()

    @property
    def placement_identity(self) -> tuple:
        """The checkpoint discard key (ticket 01). ONLY these fields invalidate a
        running investigation; memory/skills changing does not."""
        p = self.placement
        return (p.env, p.service, p.clone_path, p.repo_path, p.release_tag)


# ─────────────────────────────────────────────────────────────────────────────
# 2. What the Diagnose loop returns  —  Diagnosis | Ask | HandOver
#    (charting Q6a/Q10: reuse Friday's 3 Actions; Q6b: Ask/HandOver = loop-boundary)
# ─────────────────────────────────────────────────────────────────────────────

CONFIDENCE = ("certain", "likely", "possible", "unlikely", "guess")

@dataclass(frozen=True, slots=True)
class Diagnosis:
    """DONE — the loop reached a cause. → Report (a Reply, waits approval).
    Unchanged from today's plugins/devops/graph/diagnose.py."""
    cause: str
    confidence: Literal[CONFIDENCE]           # type: ignore[valid-type]
    conclusive: bool
    refs: list[str] = field(default_factory=list)         # line ids Lnn — grounding gate
    next_checks: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list) # ≥1 when conclusive


@dataclass(frozen=True, slots=True)
class Ask:
    """STOP-need-reporter. → Action=Ask; pool pauses; the reply resumes the loop
    from message_history iff placement_identity is unchanged (ticket 01)."""
    question: str            # what's missing, in the reporter's language
    missing: tuple[str, ...] = ()   # e.g. ("curl",) — what would unblock


@dataclass(frozen=True, slots=True)
class HandOver:
    """STOP-escalate. → Action=HandOver; operator only, never the reporter.
    For what the model should not decide (risky / out of depth / over budget)."""
    reason: str
    found_so_far: str = ""


LoopOutput = Diagnosis | Ask | HandOver   # Diagnose agent's output_type


# ─────────────────────────────────────────────────────────────────────────────
# DECIDED (ticket 03): (c)→(a) hybrid. Intake string-matches the request against
# the known service list / aliases (no model); if vague or none, it yields the
# ROOM's candidate set and the LOOP picks from that set by reading. The model
# never invents a service — it only SELECTS from a deterministic candidate set,
# so "service is never a model guess" holds while staying flexible.
# ─────────────────────────────────────────────────────────────────────────────
# THE FORK (for the record) — where does `service` come from, extraction gone?
# ─────────────────────────────────────────────────────────────────────────────
# env = domain/channel table (deterministic, settled). But "checkout is broken"
# → which canonical service? Extraction used to carry endpoint/service hints.
#
#   (a) CHANNEL TABLE: room → service(s). Deterministic, no model. If a room maps
#       to many services, Intake yields a CANDIDATE SET and the LOOP narrows by
#       reading (picks the service from the log/request). Placement then holds
#       services: tuple[...] until the loop fixes one.
#
#   (b) RESOLVE-AS-TOOL: the loop names the service; a tool resolve_placement(name)
#       does the deterministic table lookup. Keeps the LOOKUP deterministic (table
#       decides env/clone/tag) but lets the MODEL choose WHICH service to resolve.
#
#   (c) CHEAP MATCH IN INTAKE: Intake string-matches the request against the known
#       service list (no model). Works for exact/alias names, fails on vague ones
#       → then falls back to (a)'s candidate set.
#
# Trade: (a) safest, needs room→service data; (b) most flexible, loosens
# "service never a model guess"; (c) simplest, brittle on vague wording.
