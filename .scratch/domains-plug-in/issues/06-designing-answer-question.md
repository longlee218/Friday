Type: grilling
Status: open
Blocked by: 02, 04

# Designing `backend.answer_question`

## Question

A new action: "how does this business rule / endpoint work, where is it
written", answered from **code and docs**. Decide its contract — allowed step
types and toolsets (read code, read docs, what-code-means), model tier, budget,
acceptance criteria (a grounded answer citing the lines it read, like
`Diagnosis`?) — what it returns (a `Reply` that waits for approval?), and its
recognition reasoning against `trace_problem`: "it does not work" vs "how does
it work".
