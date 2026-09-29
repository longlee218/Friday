"""Helpers for which environment a domain is, and which path a curl names.

**The environment comes from the domain, by a table the operator wrote.**
Not from the reporter, who says "dev" meaning the dev *app* against
production, and not from a model, which would be asked to reproduce a lookup
it cannot be wrong about cheaply. Three outcomes and no fourth: `dev`,
`production`, `external`.

**This amends D1** ("by rule, in code"), on the operator's call of
2026-09-21. Two reasons, and the second is the one that settles it. Friday is
meant to serve more rooms than one company's, and a module naming
`aperogroup.ai` is an installation compiled into the system. And the rule was
already wrong about this company: the 2026-09-18 survey found
`api-mobile-spec-reviewer.aperogroup.ai` served from `dev` with no `.dev` in
it, and `payment-service` on two domains resolving to different endpoints. A
longest-suffix table holds the rule and its exceptions with no branch for
either — `aperogroup.ai → production`, `dev.aperogroup.ai → dev`, and the
exception is one more row that wins by being longer.

**Node 1 itself is gone (ticket 6).** `Resolve` used to also read "where it
runs" from rows the operator wrote (D3) and hand over on a missing row; that
is now the backend enricher's job (`plugins/backend/placement.py`), which imports
`domain_of`/`environment_of` from here rather than duplicating them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

__all__ = [
    "domain_of",
    "environment_of",
    "path_of",
]

_URL = re.compile(r"https?://(?P<host>[\w.-]+)")


def domain_of(curl: str | None) -> str | None:
    """The host of the request in a curl, or `None`.

    The first URL in the text, not the last: a curl carries one request, and
    anything else shaped like a URL in it is a header value or a comment.
    """
    if not curl:
        return None
    found = _URL.search(curl)
    return found.group("host").lower() if found else None


def path_of(curl: str | None) -> str | None:
    """The request's path — what the log line carries when the reporter
    pasted no correlationId (D2: a curl, *or* an endpoint plus one identifier
    the log line carries).

    Beside `domain_of` because both read the same URL, and two patterns that
    have to agree on what a URL is are two patterns that one day do not.
    """
    if not curl:
        return None
    found = _URL.search(curl)
    if not found:
        return None
    rest = curl[found.end() :]
    path = rest.split()[0].split("?")[0].strip("\"'") if rest.split() else ""
    return path or None


def environment_of(domain: str | None, known: Sequence[Any]) -> str:
    """Which environment this domain is, by the longest `environment` row
    that matches it. `external` when none does.

    Longest wins, and that is the whole mechanism: `aperogroup.ai →
    production` and `dev.aperogroup.ai → dev` express the convention, and a
    single row for one host that breaks it wins over both without any code
    knowing it is an exception.

    `external` covers a third-party service and a proxy of ours the table
    does not know yet alike, because from here they are the same thing: a
    domain nobody wrote down. Which of the two it is is the operator's call.
    """
    if not domain:
        return "external"
    matched = [
        row
        for row in known
        if domain == row.suffix or domain.endswith(f".{row.suffix}")
    ]
    if not matched:
        return "external"
    return max(matched, key=lambda row: len(row.suffix)).env
