# Flow to work

Use this protocol for non-trivial tasks.

For trivial tasks—such as changing a single line, answering a simple question, or reading code—skip all three sections, following the existing principle: **"For trivial tasks, use judgement."**

---

## 1. Declare Context — Before Starting

Fill this out immediately after understanding the request and before writing code or documentation:

```text
Goal:        <what needs to be achieved, in one sentence>
Scope:       <what is in scope> | not: <what is out of scope, even if related>
Related:     <files/modules/designs already inspected for context — use real paths>
Assumptions: <what is currently assumed to be true but has not been confirmed>
Unclear:     <anything ambiguous — if present, ask before proceeding; do not silently choose an interpretation>
```

If `Unclear` is genuinely empty—not simply ignored—it may be omitted from the visible response.

However, always ask yourself this question before starting the task.

## 2. Declare Boundaries — Before Starting

Fill this out together with Section 1:

```text
Do not touch: <files/areas outside the request, even if they seem convenient to fix>
Ask first:    <hard-to-reverse actions within this task —
               force push, deleting data, sending real messages/PRs, schema changes>
Stop if:      <signals that the task is expanding beyond scope —
               e.g. the main task requires changing >N unrelated files>
```

This is not a static checklist.

Define the boundaries specifically for each task, based on the existing principles of **Surgical Changes** and **Simplicity First**.

## 3. Self-Check — Before Reporting Completion

Fill this out before saying "done", "completed", or giving the final response:

```text
Done criteria:      <verifiable conditions, not vague goals such as "make it work">
Verified by:        <commands/tests/real observations actually performed — not assumptions>
Diff matches scope: <can every changed line be traced back to the request?
                     was anything added merely because it was convenient?>
Ticket state updated: <if this finished or advanced a ticket: which acceptance
                     boxes you ticked in .scratch/<feature>/issues/<NN>-*.md, and
                     committed with the work — or "n/a, no ticket". Never leave a
                     finished ticket's boxes unticked. See docs/agents/issue-tracker.md>
Unverified/untested: <state clearly what has not been verified, even if the task
                     is otherwise considered complete>
```

If the completion criteria are not met, do not silently lower the criteria in order to report the task as "done".

Report the actual status, including when the task is only partially complete.

---

Do not create separate state or log files for this protocol. It is not a journal.

Fill the three templates directly in the response for the task itself. Do not store them elsewhere.

Do not force the reader into this exact formatting. If the response already communicates all three sections clearly through natural prose, there is no need to reproduce the template blocks literally.

## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
