Type: prototype
Status: open
Blocked by: 10

# `trace_problem`'s graph becomes the first plan

## Question

Port the built graph `intake → acknowledge → diagnose loop → report` into the
main-flow `Plan` as the first worked example: phase 1 = agent `backend.diagnose`
with `backend.logs` + `backend.code` (its `ask_reporter` / `hand_over` terminal
tools map to `ask` / `hand_over`), phase 2 = `draft` report. Decide whether
acknowledge is a phase or a spine concern, what the plan's hypotheses and
done-criteria look like for a real case (the captured `prod-onboarding-400`),
and whether this plan is the Planner's exemplar / fallback. If it is awkward to
write, the vocabulary is wrong.
