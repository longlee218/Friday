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

**The one enforcement point — and where the responsibility actually sits:**

`validate(params)` does not live in `plan()`. It lives in the same place that
already runs the "is anything missing" check today: `_missing()` and
`plan_by_required_parameters()` in `friday/workflows/__init__.py`. That pair is
already the gate every planner goes through, and it already decides between
`Ask` (when something is missing) and the planner's verdict (when nothing is).
The engine replaces the body of that check; it does not add a new one.

Concretely: `_missing` reads `Optional[...]` annotations to find the fields
that are allowed to be None. The new engine additionally walks `_RULES` and
returns a `Problem` for each rule that fails. `_missing` returns "missing" +
"invalid"; the caller — `plan_by_required_parameters` — joins them into a
single `Ask`. A planner that wants to opt out does so by registering itself,
not by skipping validation: if a planner returns anything other than `Ask`
without going through `_missing`, it is using its own decision logic and the
caller has no way to know a value is malformed.

That is the seam. A second call site to `validate` outside `friday/workflows/`
is a test failure, not a documentation issue.

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
- [ ] `_missing()` in `friday/workflows/__init__.py` calls `validate(params)` and merges its `Problem` list into the existing "what is None" output
- [ ] `plan_by_required_parameters` returns `Ask` whenever `_missing` reports any problem (missing or invalid); the planner does not run in that case
- [ ] No other module imports `friday.validation` — enforced by a grep test
- [ ] The old "Optional means required check" path is preserved as one half of `_missing`; the new engine is the other half, not a replacement
- [ ] A test asserts that an invalid value never reaches a planner's body, even when the field is non-Optional
- [ ] No rules are written for `ApiIssueParams` or any other concrete type — that is a separate ticket (this one ships the engine only)
