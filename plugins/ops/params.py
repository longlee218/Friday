"""`ops.request_permission`'s parameters — the plugin's own, against the sdk.

Moved out of the core models in build-the-spine ticket 02, the way `trace_problem`'s
and `doc_question`'s params moved before it: the kernel keeps no task type of
its own (`skip` is not one). A plain frozen dataclass whose field metadata
(`doc`, `ask`) the extractor and the ask-renderer read — stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["AccessRequestParams"]


@dataclass(frozen=True, slots=True)
class AccessRequestParams:
    """Someone wants to be let in somewhere: a repository, an environment, a
    dashboard, a channel, an API key, a role, a permission — for themselves
    or for somebody joining. Asked outright ("can I get write access to the
    payments repo") or told as a complaint ("I cannot open the staging
    repo"); either way what unblocks them is being granted something, not
    something being fixed."""

    project: str = field(
        default="",
        metadata={
            "doc": "The project, repository or system they want access to, "
            "named as they named it. Empty if they did not say.",
            "ask": "which project you need access to",
        },
    )
    permission: str = field(
        default="",
        metadata={
            "doc": "What kind of access: read, write, admin, or their own "
            "words for it. Empty if they did not say.",
            "ask": "what access you need",
        },
    )
    summary: str = field(
        default="",
        metadata={
            "doc": "One line saying who wants what, in Vietnamese, in your own words."
        },
    )
