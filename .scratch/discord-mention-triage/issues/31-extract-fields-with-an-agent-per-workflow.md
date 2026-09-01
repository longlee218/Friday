# 31: Extract fields with an agent per workflow

**What to build:** Each workflow declares its own extraction agent — one model call,
one schema, the fields the workflow actually needs. Triage classifies; extraction
fills in. Neither does the other's job.

**Blocked by:** 30

**Status:** ready-for-agent

Today two things share `friday/triage`:

- Classification (what kind of task is this?) — that belongs in triage.
- Field extraction (correlation_id, curl, environment, etc.) — that belongs with
  the workflow that knows what those fields mean.

Extraction is a workflow concern because only the workflow knows which fields
matter and how to recognise them. Triage's regex ran in `friday/triage/params.py`
because the text was still in hand at that point, not because extraction was a
classification job. Moving extraction out is **restoring a separation that
already existed by accident**, not inventing one.

The separation also buys the right tool for each job. Regex is fast and exact
where the pattern is known. A model is flexible where the pattern is fuzzy —
"I mentioned that prod thing" vs. "I was running on prod" vs. "prod-api" as a
string. Today only the structured cases are handled; fuzzy ones silently drop
into triage as None. An agent catches the cases regex misses, at the cost of
one model call per task.

**The cost is real.** Each mention now produces two model calls: one for
classification (triage), one for extraction (workflow). Today it produces one.
The second call is **only made for messages that produced a real task** —
`skip` short-circuits both. That keeps the cost proportional to the work
actually done.

**The shape:**

- `friday/extraction.py` — small. Defines how a workflow declares its extractor
  and how `plan()` runs it. No business logic; no regex.
- Each workflow that needs fields registers an extractor the same way it
  registers a planner: a decorator, one schema, one model call. `api_issue`
  ships first because it has fields today.
- An extractor is a `Harness` instance built from a `FramingConfig` block in
  `config.yaml`, distinct from `triage` and `responder`. Its instructions are
  specific to the workflow; its schema is the workflow's `Params` type, minus
  the fields the model already wrote during triage and minus fields that are
  None-only by definition.
- The model hallucinates. That is the cost. **Validate (ticket 30) runs
  immediately after extraction**, on the merged result, so a hallucinated
  correlation_id is caught and the workflow falls back to `Ask` with a specific
  message — the same path it takes when a field is missing.

**The wiring in `plan()`:**

```
plan(task_type, params, text, agent)
   │
   ├─ extract(text, extractor)        ← new, 1 model call
   ├─ merge(triage_params, extracted)
   ├─ validate(merged_params)         ← ticket 30
   └─ planner(merged_params)
```

Validate runs after merge so a value the extractor hallucinated is caught
before the planner runs. The planner never sees a value that has not been
validated.

**Text lives on the Task.** A new column `original_text` is added to `messages`
when the message is first recorded (`Inbox._accept`). WorkflowRunner reads it
when it plans. This is a small schema change; `Inbox` is already the single
writer of that table, so the change is one line plus an Alembic revision.

The alternative — fetching text back from Discord per plan — costs an API call
and breaks for messages older than what the gateway remembers. Storing is the
right call here; the column is small.

- [x] `friday/extraction.py` exists with `@extractor(task_type)` registration and a single `extract(extractor, text) -> Params` entry point
- [x] An extractor is built from a `FramingConfig` block in `config.yaml` — separate from `triage` and `responder`
- [x] `messages.original_text` column exists; `Inbox._accept` writes it on first recording; `WorkflowRunner._plan` reads it
- [x] `plan(task_type, params, text, agent)` runs extraction, merges, validates, then dispatches to the planner
- [x] Extraction only runs for non-`skip` triage outcomes (no work, no extraction)
- [x] An `api_issue` extractor is registered that fills `environment`, `correlation_id`, `curl` from the text
- [x] Triage no longer calls `find_environment`, `find_correlation_id`, `find_curl`; those functions move to the extractor's prompt + schema
- [x] The old `params` field is no longer mutated after triage — the merge is the only place fields are filled
- [x] A test asserts that a hallucinated correlation_id from the extractor is caught by validate before the planner runs
- [x] Existing tests for triage still pass without modification
