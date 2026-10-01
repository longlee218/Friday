"""The core toolsets (build-the-spine ticket 08, `core.shell` rebuilt with
full rights in ticket 24): `core.workspace` never leaves
`/tmp/friday/<task_id>/`, one module imports pydantic-ai-harness, and `bash`
— no allowlist any more — still keeps a timeout with a process-group kill,
an output cap spilled to the workspace, redaction, the `sensitive` hook
ticket 27 fills, and an audit row for every command, not only a refusal.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from friday.kernel.audit import AuditLog
from friday.kernel.harness.harness import ModelRetry
from friday.kernel.toolsets import NotWired, core_toolsets, workspace
from friday.kernel.toolsets.bash import run as bash_run
from friday.kernel.toolsets.bash import shell_tools
from friday.kernel.toolsets.bash.exit_codes import exit_meaning
from friday.kernel.toolsets.bash.spill import MAX_CHARS
from friday.kernel.toolsets.todo import todo_tools
from friday.sdk.evidence import Evidence
from friday.sdk.redact import clear_secret_values, register_secret_values
from friday.sdk.todos import Todos
from friday.sdk.toolset import RunContext

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch) -> Path:
    """Every workspace in these tests lives under `tmp_path`."""
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path / "friday")
    return tmp_path / "friday"


def _run(task_id: int = 7) -> RunContext:
    return RunContext(
        task_id=task_id,
        domain=None,
        evidence=Evidence(),
        todos=Todos(),
        mcp={},
        reported_at=datetime(2026, 9, 29, tzinfo=UTC),
    )


def _bash(db, run=None, hosts=("local",)):
    (tool,) = shell_tools(run or _run(), hosts=hosts, audit=AuditLog(db))
    return tool


async def call(tool, **kw):
    """Invoke a tool the way the run would: `args_validator` first, then the
    function."""
    if tool.args_validator is not None:
        tool.args_validator(None, **kw)
    result = tool.function(**kw)
    return await result if asyncio.iscoroutine(result) else result


# ── exit-code meaning ───────────────────────────────────────────────────────


def test_grep_and_rg_one_means_no_matches_not_an_error():
    assert exit_meaning("grep x file", 1) == "no matches found, not an error"
    assert exit_meaning("rg x file", 1) == "no matches found, not an error"
    assert exit_meaning("grep x file", 2) is None


def test_the_last_pipeline_segment_is_what_sets_the_code():
    assert exit_meaning("cat file | grep x", 1) == "no matches found, not an error"


def test_an_unlisted_command_or_code_has_no_note():
    assert exit_meaning("ls", 1) is None
    assert exit_meaning("grep x file", 0) is None


# ── sleep used to wait, refused before anything runs ─────────────────────────


def test_sleep_to_wait_is_refused():
    tool = _bash(object())
    with pytest.raises(ModelRetry, match="sleep"):
        tool.args_validator(None, command="sleep 5")


def test_sleep_leading_a_longer_command_is_still_refused():
    tool = _bash(object())
    with pytest.raises(ModelRetry, match="sleep"):
        tool.args_validator(None, command="sleep 10 && curl x")


def test_a_short_sleep_is_allowed():
    tool = _bash(object())
    tool.args_validator(None, command="sleep 1")  # does not raise


def test_sleep_elsewhere_in_the_command_is_not_caught():
    tool = _bash(object())
    tool.args_validator(None, command="echo 'sleep 5'")  # does not raise


# ── the tool: full rights, local and over a declared host ──────────────────


async def test_bash_runs_a_command_locally(db):
    got = await call(_bash(db), command="echo hi")

    assert got.startswith("exit 0")
    assert "hi" in got


async def test_bash_routes_to_ssh_for_a_declared_host(db, monkeypatch, root):
    """No live SSH host in a test: `process.run` is replaced, and what `bash`
    calls it with is the assertion — the host, and a `ControlPath` inside
    this task's own workspace."""
    seen = {}

    async def fake_run(host, command, *, timeout, control_path):
        seen["host"], seen["command"] = host, command
        seen["control_path"] = control_path
        return 0, "ok from dev"

    monkeypatch.setattr(bash_run, "run_process", fake_run)

    got = await call(_bash(db, hosts=("local", "dev")), command="ls", host="dev")

    assert seen["host"] == "dev" and seen["command"] == "ls"
    assert seen["control_path"] is not None
    assert str(seen["control_path"]).endswith(".ssh-dev.sock")
    assert str(root / "7") in str(seen["control_path"])
    assert "ok from dev" in got


async def test_an_undeclared_host_is_refused_and_audited(db):
    got = await call(_bash(db, hosts=("local",)), command="ls", host="prod")

    assert got.startswith("refused:")
    assert "host 'prod' is not declared" in got
    (entry,) = await db.audit_entries(event="shell_refused")
    assert entry.detail["host"] == "prod"
    assert entry.detail["toolset"] == "core.shell"


async def test_a_timeout_kills_the_whole_pipeline(db, tmp_path):
    """`tail -f | grep` past the timeout: no process of the pipeline
    survives."""
    log = tmp_path / "follow-me.log"
    log.write_text("x\n")
    got = await asyncio.wait_for(
        call(_bash(db), command=f"tail -f {log} | grep x", timeout_ms=500), 5
    )

    assert "stopped after" in got
    alive = subprocess.run(
        ["pgrep", "-f", str(log)], capture_output=True, text=True, check=False
    )
    assert alive.stdout.strip() == ""


async def test_bash_caps_output_and_says_how_to_get_the_rest(db):
    got = await call(
        _bash(db), command=f"python3 -c \"print('x' * {MAX_CHARS + 5000})\""
    )

    assert "not shown" in got
    assert "save_to" in got


async def test_save_to_writes_the_whole_output_into_the_workspace(db, tmp_path, root):
    log = tmp_path / "big.log"
    log.write_text("".join(f"line {n}\n" for n in range(500)))
    run = _run()

    got = await call(_bash(db, run), command=f"cat {log}", save_to="out/big.txt")

    assert "saved to out/big.txt" in got
    assert (root / "7" / "out" / "big.txt").read_text() == log.read_text()
    assert run.evidence.index == {}


async def test_output_is_redacted_and_enters_evidence(db):
    run = _run()
    register_secret_values(["s3cr3t-value"])
    try:
        got = await call(
            _bash(db, run), command="printf 'ok\\nfailed with key s3cr3t-value\\n'"
        )
    finally:
        clear_secret_values()

    assert got.startswith("exit 0")
    assert "s3cr3t-value" not in got
    assert "failed with key [REDACTED]" in got
    assert any("[REDACTED]" in line for line in run.evidence.index.values())


async def test_exit_code_meaning_is_noted_in_the_header(db, tmp_path):
    log = tmp_path / "a.log"
    log.write_text("nothing matches here\n")

    got = await call(_bash(db), command=f"grep ZZZ {log}")

    assert got.startswith("exit 1 (no matches found, not an error)")


async def test_bash_calls_the_sensitive_check_before_it_runs(db, monkeypatch):
    seen = []

    def fake_sensitive(command, host):
        seen.append((command, host))
        return False

    monkeypatch.setattr(bash_run, "sensitive", fake_sensitive)

    await call(_bash(db), command="echo hi", host="local")

    assert seen == [("echo hi", "local")]


async def test_a_sensitive_command_is_refused_and_does_not_run(
    db, monkeypatch, tmp_path
):
    monkeypatch.setattr(bash_run, "sensitive", lambda command, host: True)
    marker = tmp_path / "marker"

    got = await call(_bash(db), command=f"touch {marker}")

    assert got.startswith("refused:")
    assert "sensitive" in got
    assert not marker.exists()
    (entry,) = await db.audit_entries(event="shell_refused")
    assert "sensitive" in entry.detail["reason"]


async def test_bash_writes_an_audit_row_for_every_command_not_only_refusals(db):
    await call(_bash(db), command="echo one")
    await call(_bash(db), command="false")

    ran = await db.audit_entries(event="shell_ran")
    assert len(ran) == 2
    assert ran[0].detail["command"] == "echo one"
    assert ran[0].detail["exit_code"] == 0
    assert ran[1].detail["command"] == "false"
    assert ran[1].detail["exit_code"] == 1
    assert all(isinstance(e.detail["duration_ms"], int) for e in ran)


def test_bash_and_todo_write_are_the_same_definition_without_a_docstring():
    """Rebuilds each tool from a copy of its own function with `__doc__`
    cleared — `function_schema` is computed once, at `Tool.__init__`, so
    clearing it after the fact (on the original) proves nothing; a second,
    fresh `Tool` built from the doc-stripped copy is what actually exercises
    whichever parser reads `__doc__`, if any still does."""
    import types

    from friday.kernel.harness.harness import tool as build_vendor_tool
    from friday.kernel.toolsets.bash.run import _bash_description, _validate_bash
    from friday.kernel.toolsets.todo.write import _validate_write, _write_description

    bash = _bash(object())
    (todo_write,) = todo_tools(_run())

    for built, description, validator in (
        (bash, _bash_description(), _validate_bash),
        (todo_write, _write_description(), _validate_write),
    ):
        fn = built.function
        copy = types.FunctionType(
            fn.__code__, fn.__globals__, fn.__name__, fn.__defaults__, fn.__closure__
        )
        copy.__annotations__ = fn.__annotations__
        copy.__doc__ = None
        stripped = build_vendor_tool(
            copy, description=description, args_validator=validator
        )
        assert built.description == stripped.description
        assert built.function_schema.json_schema == stripped.function_schema.json_schema


# ── core.todo: todo_write ────────────────────────────────────────────────────


def test_todo_write_replaces_the_whole_list():
    run = _run()
    (tool,) = todo_tools(run)

    out = asyncio.run(
        tool.function(
            todos=[
                {"content": "check the log", "status": "in_progress"},
                {"content": "read the code", "status": "pending"},
            ]
        )
    )

    assert "2 todos set, 1 in progress" in out
    assert [t.content for t in run.todos.items] == ["check the log", "read the code"]

    asyncio.run(tool.function(todos=[{"content": "only this", "status": "pending"}]))
    assert [t.content for t in run.todos.items] == ["only this"]


def test_todo_write_refuses_two_in_progress():
    run = _run()
    (tool,) = todo_tools(run)

    with pytest.raises(ModelRetry, match="in_progress"):
        tool.args_validator(
            None,
            todos=[
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ],
        )


def test_todo_write_refuses_an_unknown_status():
    run = _run()
    (tool,) = todo_tools(run)

    with pytest.raises(ModelRetry, match="status"):
        tool.args_validator(None, todos=[{"content": "a", "status": "done"}])


def test_todo_tools_falls_back_to_a_fresh_checklist_without_one():
    run = RunContext(
        task_id=1,
        domain=None,
        evidence=Evidence(),
        mcp={},
        reported_at=datetime(2026, 9, 29, tzinfo=UTC),
    )
    (tool,) = todo_tools(run)
    asyncio.run(tool.function(todos=[{"content": "a", "status": "pending"}]))
    # Nothing to assert on `run.todos` (it was never set) — the point is
    # that building and calling the tool does not raise.


# ── the workspace ───────────────────────────────────────────────────────────


async def test_a_path_escaping_the_workspace_root_is_refused(tmp_path, root):
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours")
    (files,) = workspace.workspace_tools(7)

    with pytest.raises(Exception, match="outside the root"):
        await files.read_file("../../outside.txt")
    with pytest.raises(Exception, match="outside the root"):
        await files.write_file("../escaped.txt", "x")
    assert not (root / "escaped.txt").exists()

    said = await workspace.save(7, "../../outside.txt", "overwritten")
    assert said.startswith("not saved")
    assert outside.read_text() == "not yours"


async def test_a_path_inside_the_workspace_is_read_and_written(root):
    (files,) = workspace.workspace_tools(7)
    await files.write_file("a.txt", "hello")
    assert "hello" in await files.read_file("a.txt")
    assert (root / "7" / "a.txt").read_text() == "hello"


# ── the toolsets as registered ─────────────────────────────────────────────


def test_the_core_toolsets_are_named_and_described():
    specs = {spec.name: spec for spec in core_toolsets()}
    assert set(specs) == {
        "core.memory",
        "core.memory_write",
        "core.skills",
        "core.shell",
        "core.workspace",
        "core.repos",
        "core.todo",
    }
    assert all(spec.description.strip() for spec in specs.values())


def _tool_names(built) -> set[str]:
    return {getattr(t, "name", None) or t.__name__ for t in built}


def test_core_memory_reads_and_proposes_and_core_memory_write_writes():
    """Ticket 22: the toolset is the unit of a grant, so the writes are their
    own toolset — an agent can hold the reads without them."""
    specs = {spec.name: spec for spec in core_toolsets(db=object())}
    assert _tool_names(specs["core.memory"].factory(_run())) == {
        "memory_search",
        "memory_propose",
    }
    assert _tool_names(specs["core.memory_write"].factory(_run())) == {
        "memory_add",
        "memory_update",
        "memory_delete",
    }


async def test_backend_diagnose_is_offered_memory_reads_and_no_memory_writes():
    """Ticket 22: diagnose reads data nobody vouches for, and memory outlives
    the task — so it may search and propose, never add, update or delete."""
    from friday.kernel.config import TierConfig
    from friday.kernel.harness.run_agent import run_agent
    from friday.sdk.testing import FunctionModel, ModelResponse, function_call
    from plugins.backend.actions.trace_problem import ACTION
    from plugins.backend.agents.diagnose import DIAGNOSE

    offered: list[str] = []

    def reply(messages, info):
        offered.extend(t.name for t in info.function_tools)
        return ModelResponse(parts=[function_call("hand_over", {"reason": "x"})])

    await run_agent(
        DIAGNOSE,
        TierConfig(name="flash", api_key="k", base_url="https://x.invalid", model="m"),
        ACTION.contract,
        [s for s in core_toolsets(db=object()) if s.name.startswith("core.memory")],
        _run(),
        "look",
        None,
        model=FunctionModel(reply, model_name="m"),
    )

    assert {"memory_search", "memory_propose"} <= set(offered)
    assert not {"memory_add", "memory_update", "memory_delete"} & set(offered)


async def test_backend_diagnose_is_offered_bash_and_todo_write():
    from friday.kernel.config import TierConfig
    from friday.kernel.harness.run_agent import run_agent
    from friday.sdk.testing import FunctionModel, ModelResponse, function_call
    from plugins.backend.actions.trace_problem import ACTION
    from plugins.backend.agents.diagnose import DIAGNOSE

    offered: list[str] = []

    def reply(messages, info):
        offered.extend(t.name for t in info.function_tools)
        return ModelResponse(parts=[function_call("hand_over", {"reason": "x"})])

    await run_agent(
        DIAGNOSE,
        TierConfig(name="flash", api_key="k", base_url="https://x.invalid", model="m"),
        ACTION.contract,
        [
            s
            for s in core_toolsets(db=object())
            if s.name in ("core.shell", "core.todo")
        ],
        _run(),
        "look",
        None,
        model=FunctionModel(reply, model_name="m"),
    )

    assert {"bash", "todo_write"} <= set(offered)


def test_a_core_toolset_built_without_its_dependency_says_which():
    shell = next(s for s in core_toolsets() if s.name == "core.shell")
    with pytest.raises(NotWired, match="core.shell was built without a store"):
        shell.factory(_run())


def test_the_workspace_toolset_reaches_the_agent_beside_its_tools(root):
    """A factory may hand back a whole Pydantic AI toolset; the harness puts it
    with the tool servers rather than binding it as a tool."""
    from friday.kernel.config import AgentConfig
    from friday.kernel.harness.harness import Harness

    (files,) = workspace.workspace_tools(7)
    harness = Harness(
        config=AgentConfig(name="t", api_key="k", base_url="http://x", model="m"),
        instructions="i",
        tools=[files],
    )
    assert files in harness.tool_servers
    assert harness.tools == []


# ── the guard ───────────────────────────────────────────────────────────────


def test_only_one_module_imports_pydantic_ai_harness():
    """The reuse-before-rewrite seam: one module per adopted library."""
    importers = set()
    for path in REPO.rglob("*.py"):
        relative = path.relative_to(REPO).as_posix()
        if relative.startswith((".venv/", "web/")) or "/node_modules/" in relative:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n.split(".")[0] == "pydantic_ai_harness" for n in names):
                importers.add(relative)
    assert importers == {"friday/kernel/toolsets/workspace.py"}


def test_boot_registers_the_core_toolsets_under_core(monkeypatch):
    from friday.kernel.plugin_host import load_plugins

    monkeypatch.setattr(
        "friday.kernel.plugin_host.configured_plugins", lambda config: []
    )
    loaded = load_plugins(SimpleNamespace(shell_hosts=("local",)))
    toolsets = loaded.registry.toolsets()
    assert {"core.shell", "core.workspace", "core.todo"} <= set(toolsets)
    assert loaded.registry.owner_of("core.shell").id == "core"
