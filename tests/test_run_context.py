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
SOURCES = sorted(
    path
    for where in ("friday", "tests")
    for path in (REPO / where).rglob("*.py")
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
    since the parameter can be called anything.
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
        f"something writes into the run context, which is the side channel "
        f"this board closed: {offenders}"
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


def test_every_agent_that_declares_a_context_declares_the_state():
    """One slot, one type. `FridayState` is frozen, so an agent built this way
    cannot be written into at all — which is the guarantee the test above
    approximates syntactically and this one gets by construction.

    Written out rather than derived: a check that accepted "whatever name is
    used consistently" would pass on the day somebody introduces a second
    context type and uses it everywhere, which is the exact thing being
    forbidden.
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
        f"an agent declares a run context that is not the state: {declared}"
    )


#: **There is deliberately no third guard on the word "Capture".** One was
#: written and deleted: it flagged `FieldsCapture(Model)` and `Capture(Model)`
#: in two test files, which capture a *prompt* and have nothing to do with any
#: of this — the word is not the pattern. And the pattern it was meant to catch
#: is caught the moment it matters by the two tests above: a capture nothing
#: writes into and nothing declares as a context type is harmless dead code,
#: and the instant it is wired it is one or the other. A fuzzy third guard that
#: fires on innocent code teaches a reader to edit the guard.
