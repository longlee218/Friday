"""What the SDK's per-run `context` is allowed to mean.

Board `every-answer-has-a-shape`, ticket 09 (D8, and user story 30). One slot,
one meaning: the run's `FridayState`. It used to mean two things at once — "who
is this run about" for the responder's memory tools, and "where the answer will
appear" for triage and the extractor, whose tools wrote a result into an object
the caller read back afterwards. So the classification was not the return value
of anything, and following "what did triage decide" meant knowing that a tool
wrote into a capture, that a predicate watched that capture, and that the runner
read it after the call returned. Nothing in a signature said so.

**These are guards, not behaviour.** Everything they assert is already true —
the answer tools took the captures with them in tickets 07 and 08. What is here
is the thing that says so out loud, because a rule that was only written down is
the one this repo keeps finding has drifted.

Two halves, and they close different doors — but only one of them is the real
guarantee, and it is worth being exact about which:

- **The construction.** Every agent that declares a context type declares
  `FridayState`. That class is frozen, and every one of its fields holds
  something that cannot be changed in place
  (`tests/test_friday_state.py::test_every_field_holds_something_that_cannot_be_changed_in_place`)
  — so an agent built this way cannot be written into at all. Nobody has to
  remember anything.
- **The syntax**, which is a net rather than a proof. `context_type` is
  optional, so an agent can still be handed any object through
  `run(context=...)`, and the scan below is what covers that door.

**What the syntactic half misses, stated rather than discovered later.** It
matches an assignment whose target walks through a `.context` attribute, so
`ctx.context.decided = x` is caught and these are not: aliasing first
(`state = ctx.context` then `state.decided = x`), `setattr(ctx.context, ...)`,
and in-place mutation of a field (`ctx.context.items.append(x)`). The first two
raise `FrozenInstanceError` at runtime and the third cannot arise while every
field is immutable — which is exactly why that is asserted rather than left
true by accident. Chasing every spelling here would be a losing game against a
language; the construction is what closes the door, and this is what notices
somebody propping open a different one.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Read rather than imported: the point is to catch a tool that writes into the
#: context in a module nothing here imports, and an import-based check only ever
#: sees what it already knows about.
#:
#: **Every Python file this project owns**, which `friday/` and `tests/` alone
#: were not. `run_agent.py` is the composition root — the one place adapters
#: are constructed — so an agent wired there with the wrong context type was
#: exactly the thing these guards exist to catch and exactly what they could
#: not see. `evals/` builds a real `Triage`. Both were outside the scan while
#: CLAUDE.md claimed it covered the repo.
#:
#: `migrations/` and `.agents/` are skipped for opposite reasons: a migration
#: is generated and frozen, and `.agents/` is somebody else's vendored skill
#: that happens to live here.
_SKIP = ("migrations", ".agents", ".venv", "node_modules", "web")

SOURCES = sorted(
    path
    for path in REPO.rglob("*.py")
    if not any(part in _SKIP for part in path.relative_to(REPO).parts)
)


def _trees():
    for path in SOURCES:
        yield path, ast.parse(path.read_text())


def test_nothing_writes_into_the_run_context():
    """The side channel this board closed, pinned shut.

    A tool used to answer by assigning into the object hung off `ctx.context`
    — `ctx.context.decided = Decided(...)` — and the caller read it back after
    the run. Both ends of that are gone; this is what stops a third one being
    written, which is the likely shape of the mistake: it is a small, local,
    obvious-looking way for a tool to hand something back.

    Matched on the *shape* of the assignment rather than on the name `ctx`,
    since the parameter can be called anything — which makes it deliberately
    broad: any store through an attribute named `context` fires, including one
    that has nothing to do with a run (`friday/kernel/dag/prepare.py` calls a
    `FullContext` `context`). That is the right way round for this particular
    guard, because a false positive here is a rename or a conversation and a
    false negative is the side channel coming back. The message says what it
    saw rather than what it concluded, so the conversation can happen.
    """
    offenders: dict[str, list[int]] = {}
    for path, tree in _trees():
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and _walks_through_context(node.value)
        ]
        if lines:
            offenders[str(path.relative_to(REPO))] = lines

    assert offenders == {}, (
        f"an assignment through an attribute named `context`: {offenders}. If "
        f"that is a run's context, it is the side channel this board closed "
        f"and it must not come back. If it is some other object that happens "
        f"to be called `context`, this guard is broad on purpose — say so "
        f"here rather than narrowing it."
    )


def _walks_through_context(node: ast.expr) -> bool:
    """Whether this expression reaches through a `.context` attribute.

    Walks the whole chain rather than checking one level, so
    `ctx.context.capture.decided = x` is caught along with
    `ctx.context.decided = x`.
    """
    while isinstance(node, ast.Attribute):
        if node.attr == "context":
            return True
        node = node.value
    return False


#: **What this misses, and why it is not chased.** A local alias
#: (`state = ctx.context` then `state.decided = x`) is invisible to it, and so
#: are `setattr` and mutation by method call. Two reviews found the alias
#: independently, and it is the likeliest of the three — `friday/kernel/toolsets/memory.py`
#: already opens with `state = getattr(ctx, "context", None)`, so it is the
#: shape a tool author has in front of them.
#:
#: Chasing every spelling is a losing game against a language, and it is the
#: wrong place to spend the effort: all three are closed by `FridayState` being
#: frozen with immutable fields, which is a property rather than a pattern
#: match. This catches the shape the side channel actually took here — a tool
#: writing straight into `ctx.context` — which is the shape somebody
#: reintroducing it would most likely reach for.


def test_every_agent_that_declares_a_context_declares_the_state():
    """One slot, one type. `FridayState` is frozen, so an agent built this way
    cannot be written into at all — which is the guarantee the test above
    approximates syntactically and this one gets by construction.

    Written out rather than derived: a check that accepted "whatever name is
    used consistently" would pass on the day somebody introduces a second
    context type and uses it everywhere, which is the exact thing being
    forbidden.

    **Two agents declare one today** — triage and the responder. The extractor
    and the summariser declare none at all, which is legal: `context_type` is
    the SDK's generic parameter and nothing requires it. So this binds "if you
    declare one, it is the state" and not "every agent declares one", and the
    difference is worth knowing before reading it as broader than it is.
    """
    declared: dict[str, list[str]] = {}
    for path, tree in _trees():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.keyword) and node.arg == "context_type"):
                continue
            # The bare name and nothing else. A first version walked every
            # `Name` inside the expression and subtracted `FridayState`, which
            # flagged `FridayState if self._has_memory else None` for the
            # `self` in it — and, worse, would have *passed* a conditional
            # that picked a different type on one branch. The value has to be
            # the type, not merely contain it.
            said = ast.unparse(node.value)
            if said != "FridayState":
                declared.setdefault(str(path.relative_to(REPO)), []).append(said)

    assert declared == {}, (
        # What it saw, not what it concluded. `models.FridayState` and an
        # import alias are both *the state* spelled differently, and both fail
        # here — which is the right call for a guard that has to be readable
        # rather than clever, but only if the message does not accuse them of
        # being something else.
        f"a context type that is not the bare name `FridayState`: {declared}. "
        f"If it is the state under another spelling, spell it this way; if it "
        f"is a different type, that is the thing this guard forbids."
    )


#: **There is deliberately no third guard on the word "Capture".** One was
#: written and deleted: it flagged `FieldsCapture(Model)` and `Capture(Model)`
#: in two test files, which capture a *prompt* and have nothing to do with any
#: of this — the word is not the pattern. And the pattern it was meant to catch
#: is caught the moment it matters by the two tests above: a capture nothing
#: writes into and nothing declares as a context type is harmless dead code,
#: and the instant it is wired it is one or the other. A fuzzy third guard that
#: fires on innocent code teaches a reader to edit the guard.
