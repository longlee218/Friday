Type: grilling
Status: open
Blocked by: 04, 13

# The durable spine workflow and pause/resume

## Question

Today only a task's graph (`_run_graph`) and each outbound send are DBOS
workflows; Intake/node 0 runs outside, in the pool's polling loop. Decide the
spine's durable boundary: one workflow per task from Intake to Draft? What the
pool still does (claim, concurrency, help-wanted). How `Ask` pauses from any
step and how a reporter's reply resumes it — Intake re-run, `placement_identity`
diff, continue vs re-plan — and whether this subsumes `build-the-loop` ticket 4.
