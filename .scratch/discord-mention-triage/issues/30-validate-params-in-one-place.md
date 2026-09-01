# 30: Validate params in one place

**What to build:** A single engine that turns a `Params` instance into either "fine"
or a list of specific problems, called from the planner only — not from triage,
not from the domain dataclass.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

Today two things decide whether a task is actionable, in two different places,
with two different ideas of what "missing" means.

- `Triage._hygiene` is best-effort by design — the model extracts fields from
  text, the regex overrides what it can find, and the result goes to the database
  whether or not it makes sense. Triage does not know what counts as valid; that
  is the planner's job.
- `Workflows._missing` reads `Optional[...]` annotations and reports whatever is
  None. It catches "the field is not there", not "the field is wrong" — a
  correlation id that is the string "lol" passes the missing check.

The cost of having those two checks live apart is that a malformed value travels
from triage all the way into the planner, which then has no choice but to ask
again. The cost of putting validation in triage is that the model learns to
defend against it, and it stops reporting plausible-but-wrong values.

The fix is a small engine with a closed vocabulary of rules, a way for any
`Params` class to declare its rules in one place, and a single call site that
runs them. Rules live next to the type — not in triage, not in workflows — so a
new `Params` is one file. The engine runs from the planner and refuses to let an
`Action` out while a problem is unfixed.

**Shape of the engine (proposed; can be argued down):**

- `friday/validation.py`: `Matches(pattern)`, `InSet(values)`, `OneOf(*fields)`,
  `NonEmpty()`. Each rule has a name and renders itself as the problem message it
  raises on failure (the planner turns that into a question).
- A `Params` class declares rules on its own class, conventionally `_RULES`:
  a `dict[str, Rule]`. The engine does not look at anything else; no implicit
  schema discovery, no annotation parsing. Explicit because the validation a
  field actually wants (correlation id format, environment whitelist) is not
  derivable from a type hint.
- `validate(params) -> list[Problem]` is the only function anyone outside the
  module needs. `Problem(field=..., message=...)`.
- `_missing(params)` in `friday/workflows/__init__.py` becomes a thin wrapper
  around `validate`, plus the "what is None" logic it already has.

**The one enforcement point:** `plan()` runs `validate(params)` before
dispatching to the planner; if any problem exists, `Ask` with the joined problem
messages and skip the planner entirely. Planners that do not want validation
(very few; ask first) opt out by name. No other module may call `validate`.

That is the seam. A second call site is a test failure, not a documentation
issue.

**Why it must be in the planner, not in triage:** triage's job is to extract
plausible values from text; it cannot tell a real correlation id from a
near-miss because it has no idea what a correlation id is for. The planner is
the only place that knows "to trace this, the id has to look like an id".
Pushing validation into triage means the model learns the validation rules
instead of the values, and starts hedging.

**Why not on the `Params` dataclass itself (`__post_init__`):** validation here
is a decision about whether to proceed, which depends on whether there is a
human to ask. A dataclass has no notion of "human". Keeping it outside the type
keeps the type a data shape; the engine turns that shape into a verdict.

- [ ] `friday/validation.py` exists with `Matches`, `InSet`, `OneOf`, `NonEmpty`, and `validate`
- [ ] `validate(params)` runs every rule in `_RULES` and returns a list of `Problem`, one per failure, all of them (not stop-on-first)
- [ ] A `Params` class with empty `_RULES` validates cleanly
- [ ] `plan()` in `friday/workflows/__init__.py` runs `validate(params)` before dispatching and returns `Ask` with the joined messages if any problem exists
- [ ] No other module imports `friday.validation` — enforced by a grep test
- [ ] The old "Optional means required check" path in `_missing` is still there as the underlying layer; new logic is layered on top, not replacing it
- [ ] A test asserts that an invalid value never reaches a planner's body
- [ ] No rules are written for `ApiIssueParams` or any other concrete type — that is a separate ticket (this one ships the engine only)
