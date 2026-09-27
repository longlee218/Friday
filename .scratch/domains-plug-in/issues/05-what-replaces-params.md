Type: grilling
Status: open
Blocked by:

# What replaces `params` for the responder, the board and the pool

## Question

With `Params` deleted, three readers lose "what this task knows": the
**responder** (told what the task knows before it drafts), the **board**
(shows a task's known fields), and the **pool's** log/notification line
(`known or 'nothing extracted'`). Decide what each reads instead — the intake
context, the agent's own result, a short summary the agent writes — and whether
any of it needs a schema per action at all.
