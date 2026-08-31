"""What never reaches the model.

Two reasons to decide something deterministically instead of asking. Pay talk
between colleagues is nobody's work item, and routing it through a third-party
API to be told so sends it somewhere it has no reason to go. And a rule that
runs before the call cannot be talked out of by a persuasive message.

The dangerous mistake here is the false positive: a real report skipped in
silence is a dropped mention, the one thing this system must never do. So the
rule is narrow — a compensation word standing on its own, in a message carrying
no technical signal at all. Anything with an endpoint, an environment, a status
code or a URL in it is work, whatever else it mentions.
"""

from __future__ import annotations

import re

__all__ = ["is_compensation_talk"]

_COMPENSATION = re.compile(
    r"(?<![\w-])"
    r"(lương|luong|thưởng|thuong|salary|salaries|payroll|payslip|bonus|bonuses"
    r"|compensation|raise)"
    r"(?![\w-])",
    re.IGNORECASE,
)

# A hyphen or underscore next to the word is enough to make it an identifier —
# `salary-service` is a system, not a payday.
_TECHNICAL = re.compile(
    r"https?://"
    r"|\b(api|apis|endpoint|service|server|curl|http|https|url|error|exception"
    r"|stacktrace|traceback|log|logs|deploy|deployment|staging|production|prod"
    r"|uat|sandbox|db|database|query|request|response|payload|header|token"
    r"|status|timeout|crash|bug|latency|[45]\d\d)\b",
    re.IGNORECASE,
)


def is_compensation_talk(text: str) -> bool:
    return bool(_COMPENSATION.search(text)) and not _TECHNICAL.search(text)
