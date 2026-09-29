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
| `code.py` | `backend.code` | `read_code`, `search_code`, `what_code_means` |
| `docs.py` | `backend.docs` | `read_docs` |
| `db.py` | `backend.db` | `describe_db`, `query_db` |
| `release.py` | — (a client `code`/`docs` read the running tag through) | |

Each `ToolsetSpec` declares the MCP tools it may call (`mcp={server:
TOOLS}`); the core narrows every server to exactly that before a factory sees
it, so no tool here can reach `release_rollback`.

**No agent imports.** Nothing here may reach for `friday/kernel/harness/`: a
source that can call a model is a source that can be talked into reading
something else.
"""

from __future__ import annotations

from plugins.backend.toolsets.code import CODE
from plugins.backend.toolsets.db import DB
from plugins.backend.toolsets.docs import DOCS
from plugins.backend.toolsets.logs import LOGS

__all__ = ["CODE", "DB", "DECLARED", "DOCS", "LOGS", "TOOLSETS", "declared"]

#: Every toolset the backend registers.
TOOLSETS = (LOGS, CODE, DOCS, DB)


def declared() -> frozenset[str]:
    """Every MCP tool any backend toolset says it calls — what a tool server
    is filtered down to before anything is handed it. Read off the specs
    rather than listed here: a list beside them is a list that disagrees."""
    return frozenset().union(
        *(tools for spec in TOOLSETS for tools in spec.mcp.values())
    )


#: The same set, resolved once for a caller that wants a constant.
DECLARED = declared()
