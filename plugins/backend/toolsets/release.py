"""Which version of a service is actually running.

The operator's rule, and the reason this exists: **the image tag is the
release tag**. A diagnosis read out of the clone's current HEAD is a
diagnosis of whatever the last person checked out, and on `develop` that is
routinely not what production is serving. The report already admitted as
much — "read at the clone's current HEAD, not at the version actually
running — they may differ" — and an admission is not a check.

Measured by hand on 2026-09-21: production ran `backend-reelme-v2:0.4.4`
while the clone sat on `develop`. The files that mattered were identical, so
the answer happened to hold; nobody knew that until it was checked, and
"happened to hold" is not something to put in front of an operator under
their own name.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from plugins.backend.placement import Placement

__all__ = ["RELEASE_SERVER", "ReleaseSource", "RunningVersion"]

log = logging.getLogger(__name__)

#: The MCP server `release_status` is on. A constant (build-the-spine ticket
#: 09): the name an install gives the server is the name in `mcp_servers`.
RELEASE_SERVER = "devops-generic"


@dataclass(frozen=True, slots=True)
class ReleaseSource:
    """What the cluster says it is running, through the devops MCP.

    **Read-only by construction**, like everything else in this package: the
    server this narrows also offers `release_rollout`, `release_apply`,
    `release_rollback` and `release_set_env`, and the class declaring one
    read is what keeps those out of reach.
    """

    #: **What this class may call, declared here and nowhere else.**
    TOOLS = frozenset({"release_status"})

    server: Any
    name: str = "release"
    tool: str = "release_status"

    async def running_tag(self, project: str, env: str) -> str:
        """The image tag deployed for this project, or `""`.

        `""` for every way of not knowing — the tool is missing, the release
        is not found, the answer changed shape — because the caller's job is
        the same in all of them: say the version could not be resolved and
        read what it has. A node that raised here would turn "I could not
        check which version runs" into a failed investigation.

        **The answer is enormous and almost all of it is a Helm manifest.**
        Measured 2026-09-21: 104,761 characters, of which the tag is one
        field. It is read out here rather than handed up, so nothing above
        this ever holds a rendered chart in memory or in a prompt.
        """
        try:
            answer = await self.server.call(
                self.tool, {"project": project, "env": env}
            )
        except Exception as exc:  # noqa: BLE001 — not knowing is an answer
            log.warning("release_status(%s, %s) failed: %s", project, env, exc)
            return ""
        return _tag_of(_text_of(answer))


def _tag_of(answered: str) -> str:
    """`status.config.image.tag` out of what `release_status` returned.

    Captured from the real server, 2026-09-21:
    `{"status": {"config": {"image": {"repository": …, "tag": "0.4.4"}}}}`.
    A shape that does not parse is no tag rather than an exception, for the
    reason in `running_tag`.
    """
    try:
        found = json.loads(answered)
        tag = found["status"]["config"]["image"]["tag"]
    except (TypeError, ValueError, KeyError, IndexError):
        log.warning("release_status answered a shape with no image tag in it")
        return ""
    return str(tag) if tag else ""


def _text_of(result: Any) -> str:
    """Whatever an MCP tool answered, as text. Duck-typed rather than
    imported: this package may not import `friday/kernel/harness/`."""
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else str(getattr(part, "text", part))
            for part in content
        )
    return str(content)


@dataclass
class RunningVersion:
    """Which tag each of the room's repositories runs at, asked once per run.

    A client helper, not a tool: `read_code`, `search_code` and `read_docs`
    read at this tag themselves (the operator's call, 2026-09-29), so "read
    the version that runs" is code rather than an order the prompt asks for.

    **Only the case's own repository has a known version.** The release is a
    service's, and `Placement` names one service; another repo of the room has
    no service in this case, so it is read at the checkout and says so.
    `release_status`'s `project` argument is the **service name** — an
    assumption from the image name (`backend-reelme-v2:0.4.4`), not measured;
    if it is wrong every read falls back to the checkout and says why.
    """

    release: ReleaseSource | None
    placement: Placement
    #: Per run, shared by every toolset of the run (`Evidence.tags`).
    known: dict[str, tuple[str, str]]

    async def of(self, repo: str) -> tuple[str, str]:
        """`(tag, "")`, or `("", why the version is not known)`."""
        if repo not in self.known:
            self.known[repo] = await self._ask(repo)
        return self.known[repo]

    async def _ask(self, repo: str) -> tuple[str, str]:
        placement = self.placement
        if not placement.service or repo != placement.project:
            return "", f"no service of {repo} is named in this case"
        if self.release is None:
            return "", f"{RELEASE_SERVER} is not connected"
        tag = await self.release.running_tag(placement.service, placement.env)
        if not tag:
            return "", (
                f"release_status gave no tag for {placement.service} in "
                f"{placement.env}"
            )
        return tag, ""

    @classmethod
    def for_run(cls, run: Any) -> RunningVersion:
        """From a toolset's `RunContext`: its narrowed `release_status`."""
        reads = run.mcp.get(RELEASE_SERVER)
        return cls(
            release=None if reads is None else ReleaseSource(server=reads),
            placement=run.domain,
            known=run.evidence.tags,
        )
