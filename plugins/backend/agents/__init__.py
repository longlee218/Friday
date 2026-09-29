"""The backend's agents: one module per agent, named as the agent is."""

from __future__ import annotations

from plugins.backend.agents.diagnose import DIAGNOSE
from plugins.backend.agents.explain import EXPLAIN

__all__ = ["AGENTS", "DIAGNOSE", "EXPLAIN"]

AGENTS = (DIAGNOSE, EXPLAIN)
