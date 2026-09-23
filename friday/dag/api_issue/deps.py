"""`api_issue`'s typed per-run `Deps` (ticket 13).

The base `Deps` (`friday.sdk.workflow`) carries what every node shares — the
task, the store, the tool servers, a suspended node's answers. `api_issue` is
the one task type that reaches outside the process, so its nodes need more: the
log sources to read, the release source to compare a tag against, and the two
identities its mid-run rows are queued as. Those used to travel in `deps.extra`
(a loose dict) filled from the module-global `DAG_DEPS_EXTRA`; they are fields
here now, built per run by the type's `deps` factory from the run's scope key
(DESIGN-v2 §5.2), and a boot check confirms every field can be satisfied.

`sender` and `approver` are **required** (no default) — a row queued mid-run is
sent as one of them, and getting the identity wrong is the exact bug the
`DEFAULT_SENDER`/`DEFAULT_APPROVER` split guards against. Required so a factory
that *omits* one cannot construct these `Deps`: the boot check catches the
missing field there rather than letting it surface as a mis-sent row at run.
(It is a structural check — a factory that passed an empty string would still
construct; none does.) The sources are optional: a fresh install configures
neither, and a node with nothing to read skips out loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from friday.sdk.workflow import Deps

__all__ = ["ApiIssueDeps"]


@dataclass(frozen=True, slots=True)
class ApiIssueDeps(Deps):
    #: The two identities a mid-run row is queued as (`friday.outbox`): `sender`
    #: posts into the reporter's channel as the watched account; `approver`
    #: direct-messages the operator. Required — see the module docstring.
    sender: str = field(kw_only=True)
    approver: str = field(kw_only=True)
    #: The log sources this run may read, keyed by name (`loki`/`kubectl`), or
    #: empty when none is configured. Optional — a node with none skips.
    log_sources: dict[str, Any] = field(default_factory=dict, kw_only=True)
    #: The release source a production tag is compared against, or `None`.
    release_source: Any = field(default=None, kw_only=True)
