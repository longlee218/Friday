"""`backend.k8s`: reads over the devops MCP's Kubernetes tools.

One tool today, `k8s_pod_status` — ticket 28's minimal slice, so diagnose and
explain can read a pod's own image tag when it disagrees with what
`release_status` (`../release_status.py`) says Helm deployed. Ticket 28's
`k8s_list_pods`/`k8s_list_events`/`k8s_read_configmap` slot in beside it,
each in its own file, once built.

Package rather than a flat module for the reason `core.repos`/`core.memory`
are: one file per tool, shared only once a second tool needs the same client
or renderer.
"""

from __future__ import annotations

from friday.sdk.toolset import ToolsetSpec
from plugins.backend.placement import Placement
from plugins.backend.toolsets.k8s.pod_status import (
    K8S_SERVER,
    PodStatusSource,
    k8s_tools,
)

__all__ = ["K8S", "K8S_SERVER", "k8s_tools"]

K8S = ToolsetSpec(
    name="backend.k8s",
    description=(
        "Read a pod's status directly from Kubernetes, the ground truth for "
        "what is actually running: k8s_pod_status."
    ),
    factory=k8s_tools,
    mcp={K8S_SERVER: PodStatusSource.TOOLS},
    domain_type=Placement,
)
