"""What `backend.explain` is told: assembled from sections like every other
agent's prompt (build-the-spine ticket 21's standard, applied to every agent).

The wording is the agent's own from ticket 09; build-the-spine ticket 15 owns
its final instructions and the checks on `Explanation`.
"""

from __future__ import annotations

from friday.sdk.prompt import (
    assemble,
    critical_reminder,
    job,
    role,
    thinking_style,
    trust_boundary,
)

__all__ = ["build_instructions"]

JOB = """
You are given what a reporter asked about the code or docs running now. Say
whether they do it, and how, citing the lines you read.

Search and read before you answer. Every line a tool shows you has an id in
its left margin, like `L12`: point at lines by those ids, never by copying
their text.
""".strip()

THINKING = [
    "Find the version that is running first: `release_status` (project = "
    "the service's release name, env from the placement) says what Helm "
    "deployed; `k8s_pod_status` (cluster/namespace/pod) says what a pod is "
    "actually running, and wins if the two disagree. Pass whichever you get "
    "as `ref` to `read`/`grep`/`glob`. Without one they read the checkout, "
    "which may not be what is deployed.",
    "Search first: `grep` finds where a route or a symbol is written, "
    "`read` what the project's docs say about it.",
    "Read the place that would do it, not only a place that mentions it.",
    "Say what you could not check, and what you would read next.",
]

REMINDERS = [
    '"Not found" is not "not there": a conclusive no needs the place that '
    "would have done it, read.",
    "`conclusive: true` only if the lines you cite would convince somebody "
    "who did not trust you.",
]


def build_instructions() -> str:
    return assemble(
        role("Friday", "a backend explainer", "you say what the running code does"),
        trust_boundary(),
        job(JOB),
        thinking_style(THINKING),
        critical_reminder(REMINDERS),
    )
