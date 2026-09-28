"""Core Intake and the backend enricher (build-the-spine ticket 07).

Seed → the domain's one enricher → retrieval. The three cases the prototype
printed (named service, vague, ops) and the identity diff on reply passes.
"""

from __future__ import annotations

import ast
import socket
from dataclasses import replace
from pathlib import Path

from friday.kernel.domain.conversation import ConversationId
from friday.kernel.domain.state import FridayState
from friday.kernel.spine.intake import intake
from friday.sdk.intake import ArtifactRef
from friday.sdk.memory import MemoryOrigin
from plugins.backend.placement import Placement, enrich

ROOM = "watched"
ADMIN = MemoryOrigin.ADMIN
DEV_URL = "https://api-reelme-v2.dev.aperogroup.ai/v1/pod/orders/init"
PROD_URL = "https://api-reelme-v2.aperogroup.ai/v1/pod/orders/init"
CORRELATION = "8f14e45f-ceea-467a-9b3a-1e0e4a1b2c3d"


async def _rows(db) -> None:
    state = FridayState(channel_id=ROOM, agent="admin")
    for suffix, env in (("aperogroup.ai", "production"), ("dev.aperogroup.ai", "dev")):
        await db.memory_add(state, f"{suffix} is {env}", kind="backend.environment",
                            origin=ADMIN, data={"suffix": suffix, "env": env})
    await db.memory_add(state, "the ReelMe repository", kind="backend.project", origin=ADMIN,
                        data={"name": "reelme", "repo_path": "/clone/reelme",
                              "default_branch": "main", "stack": "NestJS"})
    for name in ("backend-reelme-v2", "payments-api"):
        await db.memory_add(state, name, kind="backend.service", origin=ADMIN, data={
            "name": name, "project": "reelme",
            "prod": {"cluster": "c", "namespace": "sw", "app": name},
            "dev": {"kube_context": "dev", "namespace": "dev", "pod_pattern": name},
        })
    await db.memory_add(state, "prod logs lag two minutes", kind="fact", origin=ADMIN)
    await db.memory_add(state.for_task(1), "ERR301 was a missing selling_region",
                        kind="finding", data={"task_id": 1, "service": "backend-reelme-v2",
                                              "confidence": 0.9, "error_code": "ERR301"})
    await db.memory_add(
        state, "reelme onboarding needs selling_region", kind="skill", origin=ADMIN,
        key="reelme-onboarding",
        data={"when": {"service": ["backend-reelme-v2"], "error_codes": [],
                       "path_patterns": [], "keywords": []}},
    )


async def _said(db, *turns: str, code: tuple[str, ...] = ()) -> int:
    """A task whose reporter said `turns`, in order — replies included."""
    from conftest import make_event

    task = await db.create_task(
        conversation=ConversationId("fake", ROOM), type="backend.trace_problem",
        state="pending", confidence=0.9, params={},
    )
    for n, text in enumerate(turns):
        event = replace(make_event(channel_id=ROOM, message_id=f"m{task.id}-{n}", text=text),
                        code=code if n == 0 else ())
        await db.record_message(event)
        if n == 0:
            await db.mark_triaged(event, task.id, decision={"type": "backend.trace_problem"})
    return task.id


async def _intake(db, task_id: int, enricher=enrich):
    return await intake(db, task_id=task_id, channel_id=ROOM,
                        reported_at="2026-09-28T10:00:00+07:00", enricher=enricher)


# --- no model, no network ----------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_intake_and_the_enricher_import_no_model_and_no_reader():
    root = Path(__file__).resolve().parent.parent
    banned = ("pydantic_ai", "friday.kernel.harness", "friday.kernel.providers",
              "plugins.backend.sources", "httpx", "mcp")
    for module in ("friday/kernel/spine/intake.py", "plugins/backend/placement.py"):
        bad = [m for m in _imports(root / module) if m.startswith(banned)]
        assert bad == [], f"{module} imports {bad}"


async def test_intake_opens_no_connection(db, monkeypatch):
    await _rows(db)
    task_id = await _said(db, f"backend-reelme-v2 loi 500\n{DEV_URL}")

    def refuse(*_a, **_kw):
        raise AssertionError("intake opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    ctx = await _intake(db, task_id)

    assert ctx.domain.service == "backend-reelme-v2"


# --- the three cases ------------------------------------------------------------


async def test_a_named_service_resolves_its_placement_and_its_memory(db):
    await _rows(db)
    task_id = await _said(db, f"backend-reelme-v2 loi 500\n{DEV_URL}\n"
                              f"correlationId {CORRELATION}")

    ctx = await _intake(db, task_id)

    assert isinstance(ctx.domain, Placement)
    assert ctx.domain.env == "dev"
    assert ctx.domain.service == "backend-reelme-v2"
    assert ctx.domain.repo_path == "/clone/reelme"
    assert ctx.domain.correlation_id == CORRELATION
    assert ctx.hints.uuids == (CORRELATION,)
    assert ctx.identity == ("dev", "backend-reelme-v2", "/clone/reelme", "/clone/reelme")
    assert ctx.memory == ("prod logs lag two minutes", "ERR301 was a missing selling_region")
    assert ctx.skills == ("reelme onboarding needs selling_region",)


async def test_a_vague_report_hands_back_the_candidates_and_no_service_skill(db):
    await _rows(db)
    task_id = await _said(db, "api lỗi 400 rồi a ơi")

    ctx = await _intake(db, task_id)

    assert ctx.domain.service == ""
    assert set(ctx.domain.candidates) == {"backend-reelme-v2", "payments-api"}
    assert ctx.identity == ("external", "", "", "")
    assert ctx.memory == ("prod logs lag two minutes",)
    assert ctx.skills == ()


async def test_ops_has_no_enricher_so_no_domain_and_no_identity(db):
    from plugins.backend import PLUGIN as BACKEND
    from plugins.ops import PLUGIN as OPS

    assert OPS.enricher is None and BACKEND.enricher is enrich
    await _rows(db)
    task_id = await _said(db, "cho em xin quyền repo BE-Midas")

    ctx = await _intake(db, task_id, enricher=None)

    assert ctx.domain is None
    assert ctx.identity == ()
    assert ctx.memory == ("prod logs lag two minutes",)
    assert ctx.skills == ()


async def test_the_curl_is_the_artifact_the_store_labelled_a_curl(db):
    await _rows(db)
    curl = f'curl -X POST "{DEV_URL}"'
    task_id = await _said(db, f"backend-reelme-v2 loi\n```\n{curl}\n```", code=(curl,))

    ctx = await _intake(db, task_id)

    assert ctx.hints.artifacts, "the curl became an artifact"
    assert isinstance(ctx.hints.artifacts[0], ArtifactRef)
    assert ctx.domain.curl_artifact_id == ctx.hints.artifacts[0].id


# --- reply passes: identity -----------------------------------------------------


async def test_a_reply_adding_a_correlation_id_keeps_the_identity(db):
    await _rows(db)
    first = f"backend-reelme-v2 loi 500\n{DEV_URL}"
    before = await _intake(db, await _said(db, first))
    after = await _intake(db, await _said(db, first, f"correlationId {CORRELATION}"))

    assert after.domain.correlation_id == CORRELATION
    assert before.domain.correlation_id is None
    assert after.identity == before.identity


async def test_a_reply_pasting_a_prod_url_after_a_dev_one_changes_the_identity(db):
    """The newest turn's URL names the env, not the first one ever pasted."""
    await _rows(db)
    first = f"backend-reelme-v2 loi 500\n{DEV_URL}"
    before = await _intake(db, await _said(db, first))
    after = await _intake(db, await _said(db, first, f"nham, prod a: {PROD_URL}"))

    assert (before.domain.env, after.domain.env) == ("dev", "production")
    assert after.identity != before.identity


async def test_a_reply_naming_another_env_changes_the_identity(db):
    await _rows(db)
    first = "backend-reelme-v2 loi 500"
    before = await _intake(db, await _said(db, first))
    after = await _intake(db, await _said(db, first, f"prod a oi: {PROD_URL}"))

    assert (before.domain.env, after.domain.env) == ("external", "production")
    assert after.identity != before.identity
