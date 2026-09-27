Type: prototype
Status: open
Blocked by: 10

# `trace_problem`'s graph becomes the first plan

## Question

Port the built graph `intake → acknowledge → diagnose loop → report` into the
`Plan` shape as the first worked example: which parts become step types
(`sub_agent` for diagnose with the investigate toolset; `ask` / `hand_over` as
terminal steps; `draft` for report), whether acknowledge is a step or a spine
concern, and whether this plan is the Planner's exemplar / fallback. The test of
the vocabulary: if this plan is awkward to write, the vocabulary is wrong.
