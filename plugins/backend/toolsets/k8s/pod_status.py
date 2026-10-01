"""`backend.k8s`'s `k8s_pod_status`: what Kubernetes itself says a pod is
running — ticket 28's other half of "find the running tag before reading
code."

**`release_status` is what Helm deployed; this is what is actually
running**, and the two can disagree (a stuck rollout, a manually-patched
image). The description tells the model to prefer this tool's image tag
when they differ.

**Same name, same arguments as the MCP tool, forwarded unchanged**
(ticket 25's rule): `k8s_pod_status(cluster, namespace, pod)` calls
`devops-generic`'s `k8s_pod_status` with exactly those three arguments. No
scope check — any cluster, namespace or pod the server allows.

**Measured against the real server, 2026-09-30**: `cluster=oregon-llm,
namespace=vsl, pod=backend-reelme-v2-856bb78b6c-8qhsv` answered the pod's
own Kubernetes object — `metadata` + `status`, no wrapper — trimmed here to
`status.phase`, `conditions[]`, `hostIP`/`podIP`/`startTime` (unused),
`initContainerStatuses[]` and `containerStatuses[]`. The captured pod's
`backend-reelme-v2` container was running `…/backend-reelme-v2:0.4.9`
(`0.4.8` was `release_status`'s tag for the same service, the same day — the
two genuinely disagree, which is exactly why the description says to prefer
this one) with 9 restarts and a `lastState.terminated` of `OOMKilled`/exit
137 — diagnostic gold, kept in the render. The full response also carries
Vault's annotations, one of them a secret template: `metadata.annotations`
is never read here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from dataclasses import replace as _replace
from typing import Annotated, Any

from pydantic import Field

from friday.sdk.evidence import Evidence
from friday.sdk.toolset import RunContext, tool
from plugins.backend.placement import Placement

__all__ = ["K8S_SERVER", "PodStatusSource", "k8s_tools"]

log = logging.getLogger(__name__)

#: The MCP server `k8s_pod_status` is on — the same install as `release_status`.
K8S_SERVER = "devops-generic"


@dataclass(frozen=True, slots=True)
class PodStatusSource:
    """A pod's status, through the devops MCP.

    **Read-only by construction**, like the rest of this package: the server
    this narrows also offers `k8s_list_clusters`, `k8s_read_configmap` and
    other Kubernetes reads not built by this slice, and declaring one call
    here is what keeps them out of reach.
    """

    #: **What this class may call, declared here and nowhere else.**
    TOOLS = frozenset({"k8s_pod_status"})

    server: Any

    async def status(self, cluster: str, namespace: str, pod: str) -> Any:
        return await self.server.call(
            "k8s_pod_status",
            {"cluster": cluster, "namespace": namespace, "pod": pod},
        )


def _k8s_pod_status_description() -> str:
    return (
        "What Kubernetes itself says one pod is running right now: its "
        "phase, conditions and each container's image, restarts and state. "
        "The container image is the ground truth for what is actually "
        "running — prefer its tag over release_status's when the two "
        "disagree, since release_status is what Helm most recently "
        "deployed, not necessarily what the pod is running. This tool does "
        "not list pods; get the pod's name from a log line you have already "
        "read, or from the placement's pod name pattern."
    )


def _build_k8s_pod_status(
    domain: Placement, evidence: Evidence, source: PodStatusSource | None
):
    @tool(
        description=_k8s_pod_status_description(),
        prepare=_hint_namespace(domain),
    )
    async def k8s_pod_status(
        cluster: Annotated[
            str, Field(description="The Kubernetes cluster's name (not Loki's).")
        ],
        namespace: Annotated[str, Field(description="The pod's namespace.")],
        pod: Annotated[
            str,
            Field(
                description="The pod's exact name — from a log line you read, or "
                "the placement's pod name pattern, not this tool."
            ),
        ],
    ) -> str:
        """`backend.k8s`'s `k8s_pod_status` — see
        `_k8s_pod_status_description` for what the model is told; the
        docstring here is for a reader of this file, never the schema."""
        if source is None:
            return f"{K8S_SERVER} is not connected, so no pod can be checked."
        evidence.reads += 1
        try:
            raw = _text_of(await source.status(cluster, namespace, pod))
        except Exception as exc:  # noqa: BLE001 — not knowing is an answer
            log.warning(
                "k8s_pod_status(%s, %s, %s) failed: %s", cluster, namespace, pod, exc
            )
            return f"k8s_pod_status could not be read: {type(exc).__name__}: {exc}"
        return evidence.show(_rendered(raw, cluster, namespace, pod))

    return k8s_pod_status


def _status_of(parsed: dict[str, Any]) -> dict[str, Any]:
    """The captured shape's `status`: a raw Pod object, `metadata` beside it
    — never read here, since the full response's annotations carry a Vault
    secret template."""
    inner = parsed.get("status")
    return inner if isinstance(inner, dict) else parsed


def _state(container: dict[str, Any]) -> str:
    """One container's current state, as a phrase: `running since <time>`,
    `waiting (<reason>)`, `terminated (<reason>, exit <code>)`."""
    state = container.get("state")
    state = state if isinstance(state, dict) else {}
    name = next(iter(state), "unknown")
    detail = state.get(name)
    detail = detail if isinstance(detail, dict) else {}
    if name == "running":
        return f"running since {detail.get('startedAt', '?')}"
    if name == "waiting":
        return f"waiting ({detail.get('reason', '?')})"
    if name == "terminated":
        return f"terminated ({detail.get('reason', '?')}, exit {detail.get('exitCode', '?')})"
    return name


def _last_terminated(container: dict[str, Any]) -> str | None:
    """`lastState.terminated`, when there is one — a restart's reason and
    exit code survive the container coming back up, and an `OOMKilled` here
    is worth more than the fact that it is currently `running`."""
    last = container.get("lastState")
    terminated = last.get("terminated") if isinstance(last, dict) else None
    if not isinstance(terminated, dict):
        return None
    return (
        f"last terminated: {terminated.get('reason', '?')} "
        f"(exit {terminated.get('exitCode', '?')}) at {terminated.get('finishedAt', '?')}"
    )


def _container_line(container: dict[str, Any], *, init: bool) -> str:
    kind = "init container" if init else "container"
    bits = [
        f"{kind} {container.get('name', '?')}: image {container.get('image', '?')}, "
        f"{_state(container)}"
    ]
    if not init:
        bits.append(f"ready {container.get('ready', '?')}")
        bits.append(f"restarts {container.get('restartCount', '?')}")
        last = _last_terminated(container)
        if last:
            bits.append(last)
    return ", ".join(bits)


def _rendered(answered: str, cluster: str, namespace: str, pod: str) -> tuple[str, ...]:
    """The fields worth a model's attention: phase, the conditions that are
    not healthy, and every container's image, state and restart history —
    never `metadata` or the rest of the pod object."""
    try:
        parsed = json.loads(answered)
    except (TypeError, ValueError):
        log.warning("k8s_pod_status answered something that is not JSON")
        return (
            f"{pod} ({cluster}/{namespace}): k8s_pod_status answered something unreadable.",
        )
    if not isinstance(parsed, dict):
        return (
            f"{pod} ({cluster}/{namespace}): k8s_pod_status answered something unreadable.",
        )

    status = _status_of(parsed)
    lines = [f"{pod} ({cluster}/{namespace}): phase {status.get('phase', '?')}"]

    conditions = [c for c in (status.get("conditions") or []) if isinstance(c, dict)]
    unhealthy = [c for c in conditions if c.get("status") != "True"]
    if conditions:
        lines.append(
            f"conditions: {len(conditions) - len(unhealthy)}/{len(conditions)} healthy"
        )
    for cond in unhealthy:
        line = f"condition {cond.get('type', '?')}: {cond.get('status', '?')}"
        if cond.get("reason"):
            line += f" ({cond['reason']})"
        if cond.get("message"):
            line += f" — {cond['message']}"
        lines.append(line)

    for container in status.get("initContainerStatuses") or []:
        if isinstance(container, dict):
            lines.append(_container_line(container, init=True))
    for container in status.get("containerStatuses") or []:
        if isinstance(container, dict):
            lines.append(_container_line(container, init=False))

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


def _hint_namespace(domain: Placement):
    """A `prepare=`: the room's namespace and dev pod-name pattern, appended
    per run. Not `cluster`: `Placement.cluster` is Loki's `apero_cluster`
    label, which ticket 28 measured to differ from the Kubernetes cluster
    name — a hint that is sometimes wrong is worse than none, so the
    description says as much instead of guessing a value here."""

    async def prepare(ctx, tool_def):
        hints = []
        if domain.namespace:
            hints.append(f"namespace {domain.namespace}")
        if domain.pod_pattern:
            hints.append(f"pod name matching {domain.pod_pattern!r}")
        if not hints:
            return tool_def
        return _replace(
            tool_def,
            description=f"{tool_def.description}\n\nThis room: {', '.join(hints)}.",
        )

    return prepare


def k8s_tools(run: RunContext) -> list[Any]:
    """`backend.k8s`'s factory: `k8s_pod_status`, narrowed to `devops-generic`
    when it is open this run."""
    reads = run.mcp.get(K8S_SERVER)
    source = None if reads is None else PodStatusSource(server=reads)
    return [_build_k8s_pod_status(run.domain, run.evidence, source)]
