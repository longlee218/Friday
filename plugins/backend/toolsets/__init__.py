"""The backend's toolsets: where facts come from, and the tools that read them.

**One package touches the outside world's read surfaces** — Loki, `kubectl`,
the operator's clone, the databases — and nothing else in the plugin does
(`tests/test_sources_are_the_only_door.py` allows `plugins/*/toolsets/`). It
was `plugins/backend/sources/` beside the tools that used it; build-the-spine
ticket 09 folded the two (board `domains-plug-in` ticket 09 §3, the operator's
call: one place to control). One file per data source holds both the client
that reaches out and the tools a model calls over it:

| File | Toolset | Tools |
| --- | --- | --- |
| `logs.py` | `backend.logs` | `read_log` |
| `db.py` | `backend.db` | `describe_db`, `query_db` |
| `release_status.py` | `backend.release_status` | `release_status` |
| `k8s/` | `backend.k8s` | `k8s_pod_status` |

`code.py`/`docs.py` (`backend.code`/`backend.docs`: `read_code`, `search_code`,
`what_code_means`, `read_docs`) are gone since ticket 23: reading, searching
and listing a repository is generic now (`core.repos`,
`friday/kernel/toolsets/repos/`); this plugin's own contribution to it is
`Placement.repos()` (the sdk's `RepoRoom`). `core.repos`'s `read`/`grep`/`glob`
take an optional `ref` the model fills in itself, from `release_status` or
`k8s_pod_status` (ticket 28's minimal slice) — there is no plugin-side
running-version lookup any more (`backend.release`/`RunningVersion`, deleted
per the operator's 2026-09-30 call: the model finds the tag with tools, not
code).

Each `ToolsetSpec` declares the MCP tools it may call (`mcp={server:
TOOLS}`); the core narrows every server to exactly that before a factory sees
it, so no tool here can reach `release_rollback`.

**No agent imports.** Nothing here may reach for `friday/kernel/harness/`: a
source that can call a model is a source that can be talked into reading
something else.
"""

from __future__ import annotations

from plugins.backend.toolsets.db import DB
from plugins.backend.toolsets.k8s import K8S
from plugins.backend.toolsets.logs import LOGS
from plugins.backend.toolsets.release_status import RELEASE_STATUS

__all__ = ["DB", "DECLARED", "K8S", "LOGS", "RELEASE_STATUS", "TOOLSETS", "declared"]

#: Every toolset the backend registers.
TOOLSETS = (LOGS, DB, RELEASE_STATUS, K8S)


def declared() -> frozenset[str]:
    """Every MCP tool any backend toolset says it calls — what a tool server
    is filtered down to before anything is handed it. Read off the specs
    rather than listed here: a list beside them is a list that disagrees."""
    return frozenset().union(
        *(tools for spec in TOOLSETS for tools in spec.mcp.values())
    )


#: The same set, resolved once for a caller that wants a constant.
DECLARED = declared()
