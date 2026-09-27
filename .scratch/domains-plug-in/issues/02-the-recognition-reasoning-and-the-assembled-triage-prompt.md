Type: prototype
Status: open
Blocked by:

# The recognition reasoning and the assembled triage prompt

## Question

Stub, to react to: the exact shape of an action's **recognition reasoning**
(when to pick it, when not and which action instead, a few examples), and the
triage prompt the core **assembles** from the domain-agnostic reasoning + every
registered action's reasoning + operator-confirmed DB examples — with **no
order and no catch-all**.

Show the assembled prompt for `backend.trace_problem`, `backend.answer_question`
and `ops.request_permission`, and how the label meanings that today come from
`Params` docstrings (deleted with the extractor) move into it. Decide the
boot-time checks (an example whose label is not registered refuses the boot).
