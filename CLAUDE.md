# CLAUDE.md

How to work in this repository. Nothing else lives here: no project state,
no architecture, no layout.

@.claude/AGENT-PROTOCOL.md

## Where things are written

| Question | File |
| --- | --- |
| What is running, which boards are open, what is next | `CONTEXT.md` § Project state |
| What a word means in this domain | `CONTEXT.md` § Vocabulary |
| How the system is built, where code lives, which rules are load-bearing | `docs/DESIGN.md` § What exists |
| Why a decision was made | `docs/DESIGN.md` § Reasoning |
| What a piece of work is | the ticket, under `.scratch/<feature-slug>/issues/` |

Read `docs/DESIGN.md` § What exists before adding anything non-trivial; if a
decision recorded there looks wrong, raise it rather than building around
it. When you change a load-bearing decision, correct `docs/DESIGN.md` in the
same commit, and when you finish or open work, correct `CONTEXT.md` —
nothing breaks when either is wrong, which is how they go stale. Read
`CONTEXT.md` § Vocabulary before naming anything, and add the term there
when you name something new.

### Issue tracker

Local markdown under `.scratch/<feature-slug>/issues/`, one board per
feature, each with its spec beside its issues; `docs/SPEC.md` is the
original board's. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles: `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` at the repo root. `docs/adr/` does not exist
yet. See `docs/agents/domain.md`.

## Commands

```bash
uv sync                          # create/update .venv from pyproject + uv.lock
uv run pytest -q                 # the whole suite
uv run run_agent.py              # run it
uv add <package>                 # add a dependency; never hand-edit pyproject.toml
cd web && npm install            # once
cd web && npm run build          # -> web/dist, served at /
uv run serve_board.py            # the API alone, for `cd web && npm run dev`

# migrations: autogenerate against a throwaway db, never the live one
FRIDAY_DB=/tmp/new.db uv run alembic upgrade head
FRIDAY_DB=/tmp/new.db uv run alembic revision --autogenerate -m "what changed"
uv run alembic current           # where the live db is
```

## Verifying a change

A ticket, an edit to logic, a refactor — none of them are done until:

1. **The whole suite passes.** `uv run pytest -q`, not a `-k` subset. Most of
   the rules in `docs/DESIGN.md` are enforced by a test rather than by
   memory, so a green suite is the only evidence that they survived your
   change.
2. **A subagent has checked the change, not you.** `code-review` for the
   Python — a second read catches what the person who just wrote it stops
   seeing.
3. **Any guard you added has been deleted once and watched go red.** A test
   that still passes without its guard was testing nothing.
4. **A change to `friday/triage/prompt.py`, or to anything upstream of it,
   also needs `uv run python -m evals.run_triage_eval` run against
   `evals/triage.jsonl`, with the accuracy, confusion matrix and threshold
   table reported alongside the change.** The suite's scripted transport
   pins wiring and says nothing about whether the classifier is right; this
   is the only thing that does. See `evals/README.md`.

Report what the suite actually said. A step you skipped is worth saying out
loud; a failing test reported as passing is the one failure this file cannot
catch.
