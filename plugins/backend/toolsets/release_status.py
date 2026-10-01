"""`backend.release_status`: what Helm says is deployed for one of the room's
services — the minimal slice of ticket 28 that gives diagnose and explain a
tag to pass as `ref` to `core.repos` (ticket 23's own D7 amendment).

**Same name, same arguments as the MCP tool, forwarded unchanged** (25's
rule, extended to 28 by the operator, 2026-09-30): `release_status(project,
env)` calls `devops-generic`'s `release_status` with exactly those two
arguments. No scope check — any project or env the server allows.

**The answer is enormous and almost all of it is a Helm manifest.** Measured
against the live server, 2026-09-30: `backend-reelme-v2` in `prod` came back
135,799 characters. The useful fields are `status.config.image.repository`,
`status.config.image.tag`, `status.info.status`, `status.info.last_deployed`
and `history[]` (ten revisions, each `revision`/`status`/`chart`/
`app_version`/`updated`/`description`) — the manifest itself never reaches
the model. `env=dev` for the same project answered `{"status": {},
"history": []}`: rendered as "no release found", not an error.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from dataclasses import replace as _replace
from typing import Annotated, Any

from pydantic import Field

from friday.sdk.evidence import Evidence
from friday.sdk.toolset import RunContext, ToolsetSpec, tool
from plugins.backend.placement import Placement

__all__ = [
    "RELEASE_SERVER",
    "RELEASE_STATUS",
    "ReleaseSource",
    "release_status_tools",
]

log = logging.getLogger(__name__)

#: The MCP server `release_status` is on (build-the-spine ticket 09: an
#: install fact, not a setting a run chooses).
RELEASE_SERVER = "devops-generic"

#: How many of `history`'s revisions are shown before the rest are just
#: counted. Ten come back; showing all of them is a rollout log, not a
#: version check.
MAX_HISTORY = 5


@dataclass(frozen=True, slots=True)
class ReleaseSource:
    """What Helm says is deployed, through the devops MCP.

    **Read-only by construction**, like the rest of this package: the server
    this narrows also offers `release_rollout`, `release_apply`,
    `release_rollback` and `release_set_env`, and declaring one read here is
    what keeps those out of reach.
    """

    #: **What this class may call, declared here and nowhere else.**
    TOOLS = frozenset({"release_status"})

    server: Any

    async def status(self, project: str, env: str) -> Any:
        return await self.server.call(
            "release_status", {"project": project, "env": env}
        )


def _release_status_description() -> str:
    return (
        "What Helm says is deployed for one of the room's services: the "
        "image tag actually running, its rollout status and recent history. "
        "Use the tag as `ref` to `core.repos`'s `read`/`grep`/`glob`, so code "
        "is read at the version that is running rather than the clone's "
        "checkout. If `k8s_pod_status`'s own image tag disagrees with this "
        "one, prefer the pod's — it is what is actually running, where this "
        "is what Helm most recently deployed."
    )


def _build_release_status(
    domain: Placement, evidence: Evidence, source: ReleaseSource | None
):
    @tool(description=_release_status_description(), prepare=_hint_service(domain))
    async def release_status(
        project: Annotated[
            str,
            Field(
                description="The service's release name, as Helm knows it "
                "— the room's resolved service."
            ),
        ],
        env: Annotated[str, Field(description='"dev" or "prod".')],
    ) -> str:
        """`backend.release_status`'s `release_status` — see
        `_release_status_description` for what the model is told; the
        docstring here is for a reader of this file, never the schema."""
        if source is None:
            return f"{RELEASE_SERVER} is not connected, so no release can be checked."
        evidence.reads += 1
        try:
            raw = _text_of(await source.status(project, env))
        except Exception as exc:  # noqa: BLE001 — not knowing is an answer
            log.warning("release_status(%s, %s) failed: %s", project, env, exc)
            return f"release_status could not be read: {type(exc).__name__}: {exc}"
        return evidence.show(_rendered(raw, project, env))

    return release_status


def _or(value: Any, default: str = "?") -> Any:
    """`value`, or `default` — for a field that is missing *or* explicitly
    `null`, which `dict.get(key, default)` only covers the first of (code
    review finding on the same gap in `config`, above)."""
    return value if value is not None else default


def _rendered(answered: str, project: str, env: str) -> tuple[str, ...]:
    """The fields worth a model's attention, never the manifest around them.

    Captured shape (2026-09-30): `{"status": {"config": {"image":
    {"repository": …, "tag": …}}, "info": {"status": …, "last_deployed":
    …}}, "history": [{"revision": …, "status": …, "chart": …,
    "app_version": …, "updated": …, "description": …}, …]}`. `env=dev`
    answered `{"status": {}, "history": []}` for the same project — an empty
    `status` is "no release", not a shape to parse further.
    """
    try:
        parsed = json.loads(answered)
    except (TypeError, ValueError):
        log.warning("release_status answered something that is not JSON")
        return (f"{project} ({env}): release_status answered something unreadable.",)
    if not isinstance(parsed, dict):
        return (f"{project} ({env}): release_status answered something unreadable.",)

    status = parsed.get("status") or {}
    config = (status.get("config") or {}) if isinstance(status, dict) else {}
    image = config.get("image") if isinstance(config, dict) else None
    if not isinstance(image, dict):
        return (f"{project}: no release found in {env}.",)

    info = status.get("info") or {}
    lines = [
        f"{project} ({env}):",
        f"image: {_or(image.get('repository'))}:{_or(image.get('tag'))}",
        f"status: {_or(info.get('status'))}, last deployed {_or(info.get('last_deployed'))}",
    ]
    history = parsed.get("history")
    history = history if isinstance(history, list) else []
    for entry in history[:MAX_HISTORY]:
        if not isinstance(entry, dict):
            continue
        line = (
            f"revision {_or(entry.get('revision'))}: {_or(entry.get('status'))} "
            f"(chart {_or(entry.get('chart'))}, app {_or(entry.get('app_version'))}, "
            f"{_or(entry.get('updated'))})"
        )
        if entry.get("description"):
            line += f" — {entry['description']}"
        lines.append(line)
    if len(history) > MAX_HISTORY:
        lines.append(f"… {len(history) - MAX_HISTORY} older revisions not shown.")
    return tuple(lines)


def _text_of(result: Any) -> str:
    """Whatever an MCP tool answered, as text.

    Duck-typed rather than imported: `friday/kernel/harness/harness.py` is the
    one module that may import the agent SDK, and this package may not
    import `friday/kernel/harness/` at all.
    """
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else str(getattr(part, "text", part))
            for part in content
        )
    return str(content)


def _hint_service(domain: Placement):
    """A `prepare=`: the room's resolved service, appended per run — the
    hint the model needs for `project` without discovering it itself."""

    async def prepare(ctx, tool_def):
        if not domain.service:
            return tool_def
        return _replace(
            tool_def,
            description=f"{tool_def.description}\n\nThis room's service: "
            f"{domain.service} (env: {domain.env}).",
        )

    return prepare


def release_status_tools(run: RunContext) -> list[Any]:
    """`backend.release_status`'s factory: `release_status`, narrowed to
    `devops-generic` when it is open this run."""
    reads = run.mcp.get(RELEASE_SERVER)
    source = None if reads is None else ReleaseSource(server=reads)
    return [_build_release_status(run.domain, run.evidence, source)]


RELEASE_STATUS = ToolsetSpec(
    name="backend.release_status",
    description=(
        "What Helm says is deployed for one of the room's services, so a "
        "diagnosis can read code at the version that is running: release_status."
    ),
    factory=release_status_tools,
    mcp={RELEASE_SERVER: ReleaseSource.TOOLS},
    domain_type=Placement,
)
