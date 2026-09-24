"""Seam S4 — the dependency rule, and G1 "the kernel names no plugin".

The whole restructure rests on one direction of dependency: `sdk` is the bottom
of our own code — Protocols plus the pure, dependency-free values and helpers
everything shares — `kernel` builds on `sdk`, and a plugin builds on `sdk` only —
never the reverse, never sideways. Stated in prose it rots; read with `ast` it
cannot, because an import written any way is still an import. Extends the
`test_sources_are_the_only_door` / `test_composition_root` pattern (spec §
Testing Decisions, S4).

**`sdk` imports nothing of ours.** Ticket 19 folded `friday.domain` away: its
pure pieces (the actions, the validation DSL, the prompt primitives, `scrub`,
the memory `Origin`) moved *into* `sdk`, and its models moved *under* `kernel`
(`friday.kernel.domain`). So there is no value layer beneath the sdk any more —
the sdk is it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The core memory kinds — every install has them, they stay unprefixed, and no
#: plugin owns them (DESIGN-v2 §9.2). Everything else the memory registry holds
#: is a pack kind a plugin ships, so its bare name is a literal the kernel must
#: not carry.
CORE_KINDS = {"fact", "constraint", "decision", "voice", "summary", "finding", "person", "skill"}


def _forbidden_literals() -> set[str]:
    """The literals G1 forbids inside `friday/kernel`: a task type's name or a
    pack kind's name hardcoded there is the core knowing a plugin by name. Both
    come from their registries (the autouse fixture fills them), not a
    hand-maintained catalog — the point of tickets 11 and 12."""
    from friday.dag import registry
    from friday.memory import registry as memory_registry

    pack_kinds = set(memory_registry.kinds()) - CORE_KINDS
    return set(registry.decision_params()) | pack_kinds


def _modules(base: Path) -> list[tuple[str, ast.Module]]:
    """Every `.py` under `base`, as (repo-relative posix path, parsed tree)."""
    out = []
    if not base.exists():
        return out
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "__pycache__" in rel:
            continue
        out.append((rel, ast.parse(path.read_text())))
    return out


def _friday_imports(rel: str, tree: ast.Module) -> set[str]:
    """The `friday....` (and `plugins....`) modules this file imports, with
    relative imports resolved against the file's own package."""
    pkg = Path(rel).parent.parts  # e.g. ("friday", "sdk")
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(("friday", "plugins")):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                mod = node.module or ""
            else:
                base = pkg[: len(pkg) - (node.level - 1)]
                mod = ".".join((*base, node.module) if node.module else base)
            if mod.startswith(("friday", "plugins")):
                found.add(mod)
    return found


def _under(module: str, *prefixes: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def test_sdk_imports_nothing_of_ours():
    """`sdk` is the bottom of our own code: it imports only itself of ours —
    nothing above it, and (since ticket 19 folded `friday.domain` away) no value
    layer beneath it either."""
    offenders = {
        (rel, mod)
        for rel, tree in _modules(ROOT / "friday" / "sdk")
        for mod in _friday_imports(rel, tree)
        if not _under(mod, "friday.sdk")
    }
    assert offenders == set(), f"sdk reached outside itself: {sorted(offenders)}"


def test_kernel_imports_only_sdk():
    """`kernel` builds on `sdk` (and itself). It must not reach into a plugin or
    the application modules that are being pulled out from under it."""
    offenders = {
        (rel, mod)
        for rel, tree in _modules(ROOT / "friday" / "kernel")
        for mod in _friday_imports(rel, tree)
        if not _under(mod, "friday.sdk", "friday.kernel")
    }
    assert offenders == set(), f"kernel imported something other than sdk: {sorted(offenders)}"


def test_a_plugin_imports_sdk_only():
    """A plugin codes against `sdk` and nothing else *of ours* — the reason it is
    detachable. Its own package is not "ours": a plugin freely imports its own
    submodules (`plugins.devops.graph.resolve` from `plugins.devops.graph`). What
    it may not reach for is the core (anything `friday.*` but `friday.sdk`) or
    another plugin. Non-vacuous since ticket 14 landed `plugins/devops/`."""
    offenders = set()
    for rel, tree in _modules(ROOT / "plugins"):
        parts = Path(rel).parts  # ("plugins", "devops", …)
        own = ".".join(parts[:2]) if len(parts) >= 2 else "plugins"
        for mod in _friday_imports(rel, tree):
            if not _under(mod, "friday.sdk", own):
                offenders.add((rel, mod))
    assert offenders == set(), f"a plugin imported more than sdk: {sorted(offenders)}"


def test_the_kernel_imports_no_plugin():
    """G1, half one: the kernel names no plugin by import."""
    offenders = {
        (rel, mod)
        for rel, tree in _modules(ROOT / "friday" / "kernel")
        for mod in _friday_imports(rel, tree)
        if _under(mod, "friday.plugins", "plugins")
    }
    assert offenders == set(), f"the kernel imported a plugin: {sorted(offenders)}"


def test_the_kernel_carries_no_task_type_or_pack_kind_literal():
    """G1, half two: no task-type or pack-kind name hardcoded in `friday/kernel`.
    A string constant equal to one is the core knowing a plugin's id — the guard
    that keeps the kernel detachable from what registers against it."""
    forbidden = _forbidden_literals()
    offenders = {
        (rel, node.value)
        for rel, tree in _modules(ROOT / "friday" / "kernel")
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in forbidden
    }
    assert offenders == set(), f"the kernel hardcoded a plugin's id: {sorted(offenders)}"
