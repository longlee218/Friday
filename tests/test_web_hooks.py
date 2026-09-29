"""React's one rule, checked here because nothing else checks it.

A component must call the same hooks in the same order on every render. Break
it and React does not warn — it throws, the tree unmounts, and the page renders
*nothing*. From the outside that looks exactly like a backend problem: the API
answers correctly and the screen stays blank.

**This shipped.** `FlowScreen`'s `Path` called `useEffect` below two early
returns, so the first render — data not yet in — ran two hooks and returned a
skeleton, and the render after the fetch resolved ran three. Every visit to
`/flow/{provider}/{id}` died the moment its data arrived. It survived a code
review, a green suite and an operator using the rest of the board daily,
because:

- there are no JavaScript tests here, deliberately (`CLAUDE.md`);
- there is no linter, so `eslint-plugin-react-hooks` — which exists for
  precisely this and would have caught it in a second — never ran;
- and the Python suite reads this code for hex literals and for API field
  names, neither of which is this.

So this is the third Python test that reads the TypeScript, and it is the same
bargain the other two make: a real linter would do it better, and until there
is one, a narrow guard that catches the shape this bug actually took is worth
more than the rule being written down and unenforced.

**Narrow on purpose.** It flags one shape: a hook called at a component's own
statement level, after an `if` block at that level that returns. That is the
early-return-then-hook mistake. It does not attempt to be
`rules-of-hooks` — a hook in a loop, in a nested callback, or behind a ternary
all pass here and are all still wrong. Replacing this with the real plugin is
an improvement, not a duplication.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web" / "src"

#: A component's own statements sit at one indent under `function name(...) {`.
#: Prettier formats this tree, so indentation is structure here rather than a
#: hopeful proxy for it.
_BODY = "  "
_HOOK = re.compile(r"^ {2}(?:const|let|var)?\s*.*?\buse[A-Z]\w*\(")
_FUNCTION = re.compile(r"^(?:export\s+)?function\s+(\w+)")


def _offenders(source: str) -> list[tuple[str, int, str]]:
    """`(component, line number, the offending line)` for each violation."""
    lines = source.splitlines()
    found: list[tuple[str, int, str]] = []
    component: str | None = None
    returning = False  # an `if` at body level has returned above us
    in_if = False

    for number, line in enumerate(lines, 1):
        if (start := _FUNCTION.match(line)) is not None:
            component, returning, in_if = start.group(1), False, False
            continue
        if component is None:
            continue
        if line and not line.startswith(" ") and not line.startswith("}"):
            component = None
            continue

        # An `if` at the component's own level, and where it closes.
        if line.startswith((f"{_BODY}if (", f"{_BODY}}} else")):
            in_if = True
        elif line == f"{_BODY}}}":
            in_if = False
        elif in_if and re.match(r"^ {4,}return\b", line):
            returning = True

        if returning and _HOOK.match(line):
            found.append((component, number, line.strip()))
    return found


def test_no_component_calls_a_hook_after_an_early_return():
    offenders: dict[str, list[tuple[str, int, str]]] = {}
    for path in sorted(WEB.rglob("*.tsx")):
        if bad := _offenders(path.read_text()):
            offenders[str(path.relative_to(WEB))] = bad

    assert offenders == {}, (
        f"a hook is called after an early return, so the render that takes the "
        f"other branch runs a different number of hooks and React throws — the "
        f"screen renders nothing and it reads as a backend fault: {offenders}"
    )


def test_the_guard_sees_the_shape_that_actually_shipped():
    """The guard on the guard, written from the real defect rather than from an
    invented one: this is `FlowScreen`'s `Path` as it was, reduced."""
    shipped = """
function Path({ provider, id }: { provider: string; id: string }) {
  const flow = useAsync(() => api.flow(provider, id), [provider, id]);

  if (!flow.value) {
    return <Skeleton />;
  }

  useEffect(() => {
    if (!flow.value) return;
  }, [flow.value]);

  return <div />;
}
"""
    ((component, _, line),) = _offenders(shipped)

    assert component == "Path"
    assert "useEffect" in line


def test_a_hook_before_every_return_is_not_flagged():
    """The fix, and the ordinary shape of every other screen here."""
    fixed = """
function Path({ provider, id }: { provider: string; id: string }) {
  const flow = useAsync(() => api.flow(provider, id), [provider, id]);

  useEffect(() => {
    if (!flow.value) return;
  }, [flow.value]);

  if (!flow.value) {
    return <Skeleton />;
  }

  return <div />;
}
"""
    assert _offenders(fixed) == []


def test_a_return_inside_a_callback_is_not_an_early_return():
    """The false positive worth refusing: a `return` inside an arrow function
    passed to `map` is that callback's, not the component's, and a hook after
    it is fine. A guard that fired here would be edited away rather than
    obeyed."""
    ordinary = """
function List({ items }: { items: number[] }) {
  const doubled = items.map((i) => {
    return i * 2;
  });

  const [open, setOpen] = useState(false);

  return <div>{doubled.length}</div>;
}
"""
    assert _offenders(ordinary) == []
