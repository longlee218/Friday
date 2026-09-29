"""build-the-spine ticket 09 — the backend's toolsets, folded out of `sources/`.

What these guard: a factory reaches only the MCP tools its own toolset
declared (the core narrows every server per toolset); the code and docs tools
read at the version that is running, never the working copy, and say so when
they cannot; `repo` is one of the room's projects and nothing else.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from datetime import UTC, datetime

import pytest

from friday.kernel.harness.run_agent import build_tools, reads_for
from friday.sdk.toolset import RunContext
from plugins.backend.placement import Placement, Project
from plugins.backend.toolsets import CODE, DB, DOCS, LOGS, TOOLSETS
from plugins.backend.toolsets.evidence import Evidence

AT = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


class Devops:
    """The devops-generic server as the core holds it: it offers the writes
    too, and answers `release_status` with the tag it was given."""

    def __init__(self, tag: str = "1.0.0") -> None:
        self.tag = tag
        self.called: list[str] = []

    async def call_tool(self, tool, arguments):
        self.called.append(tool)
        if tool == "release_status":
            return json.dumps({"status": {"config": {"image": {"tag": self.tag}}}})
        return "done"


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )


@pytest.fixture
def clone(tmp_path):
    """A clone whose tag `1.0.0` differs from its working copy."""
    root = tmp_path / "reelme"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "src" / "orders.ts").write_text("released\ncheckToken()\n")
    (root / "docs" / "webhooks.md").write_text("# Webhooks\nsigned at 1.0.0\n")
    (root / "README.md").write_text("not a doc\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "one")
    _git(root, "tag", "1.0.0")
    (root / "src" / "orders.ts").write_text("edited since\nnoToken()\n")
    (root / "docs" / "webhooks.md").write_text("# Webhooks\nedited since\n")
    return root


def _placement(clone, *, service="backend-reelme-v2") -> Placement:
    return Placement(
        env="production",
        service=service,
        project="reelme",
        repo_path=str(clone),
        clone_path=str(clone),
        projects=(
            Project(name="reelme", repo_path=str(clone), docs_paths=("docs",)),
            Project(name="other", repo_path=str(clone), docs_paths=("docs",)),
        ),
    )


def _tools(toolsets, placement, servers, evidence=None):
    run = RunContext(
        task_id=1,
        domain=placement,
        evidence=evidence or Evidence(),
        mcp={},
        reported_at=AT,
    )
    from friday.kernel.harness.harness import _bind_tool_spec

    return {
        t.name: t
        for t in (_bind_tool_spec(x) for x in build_tools(toolsets, run, servers))
    }


def call(tool, **kw):
    result = tool.function(**kw)
    return asyncio.run(result) if inspect.isawaitable(result) else result


# --- a factory reaches only its own declared reads ---------------------------


def test_a_factory_cannot_reach_release_rollback():
    """The server offers `release_rollback`; `backend.code` declared only
    `release_status`, so the `Reads` its factory is handed refuses the write
    in this process — whatever the server would have done."""
    server = Devops()
    seen = {}

    def factory(run):
        seen.update(run.mcp)
        return []

    from dataclasses import replace

    build_tools(
        [replace(CODE, factory=factory)],
        RunContext(
            task_id=1,
            domain=None,
            evidence=None,
            mcp={},
            reported_at=AT,
        ),
        {"devops-generic": server},
    )

    reads = seen["devops-generic"]
    assert reads.allowed == frozenset({"release_status"})
    with pytest.raises(PermissionError):
        asyncio.run(reads.call("release_rollback", {}))
    assert server.called == []


def test_each_toolset_is_narrowed_to_its_own_reads():
    """Per toolset, not per plugin: `backend.logs` never sees
    `release_status`, and a server a toolset did not declare is absent."""
    servers = {"devops-generic": Devops(), "db-generic": object()}

    assert reads_for(LOGS, servers)["devops-generic"].allowed == {"loki_query_range"}
    assert reads_for(CODE, servers)["devops-generic"].allowed == {"release_status"}
    assert set(reads_for(LOGS, servers)) == {"devops-generic"}
    assert set(reads_for(DB, servers)) == {"db-generic"}
    assert reads_for(CODE, {}) == {}


def test_the_plugin_registers_every_toolset_under_its_own_name():
    assert [t.name for t in TOOLSETS] == [
        "backend.logs",
        "backend.code",
        "backend.docs",
        "backend.db",
    ]
    assert all(t.domain_type is Placement for t in TOOLSETS)


# --- the running version -----------------------------------------------------


def test_read_code_reads_at_the_running_tag_not_the_working_copy(clone):
    tools = _tools([CODE], _placement(clone), {"devops-generic": Devops("1.0.0")})
    head = _git(clone, "rev-parse", "HEAD").stdout

    said = call(tools["read_code"], repo="reelme", file="/app/src/orders.ts", line=1)

    assert "released" in said and "edited since" not in said
    assert "at 1.0.0" in said
    # `git show`, never a checkout: the operator's tree and HEAD are untouched.
    assert (clone / "src" / "orders.ts").read_text().startswith("edited since")
    assert _git(clone, "rev-parse", "HEAD").stdout == head


def test_the_running_tag_is_asked_once_per_run(clone):
    server = Devops("1.0.0")
    tools = _tools([CODE], _placement(clone), {"devops-generic": server})

    call(tools["read_code"], repo="reelme", file="/app/src/orders.ts", line=1)
    call(tools["search_code"], repo="reelme", query="Token")

    assert server.called == ["release_status"]


def test_no_devops_server_reads_the_checkout_and_says_why(clone):
    evidence = Evidence()
    tools = _tools([CODE], _placement(clone), {}, evidence)

    said = call(tools["read_code"], repo="reelme", file="/app/src/orders.ts", line=1)

    assert "edited since" in said
    assert "running version unresolved: devops-generic is not connected" in said
    assert any("not the running version" in line for line in evidence.not_checked)


def test_a_tag_the_clone_does_not_have_falls_back_and_says_so(clone):
    tools = _tools([CODE], _placement(clone), {"devops-generic": Devops("9.9.9")})

    said = call(tools["read_code"], repo="reelme", file="/app/src/orders.ts", line=1)

    assert "edited since" in said
    assert "fetch its tags" in said


def test_another_repo_of_the_room_has_no_known_version(clone):
    """The release is the case's service's; another repo has no service here,
    so it is read at the checkout rather than at somebody else's tag."""
    server = Devops("1.0.0")
    tools = _tools([CODE], _placement(clone), {"devops-generic": server})

    said = call(tools["read_code"], repo="other", file="src/orders.ts", line=1)

    assert "no service of other is named in this case" in said
    assert server.called == []


def test_search_code_finds_at_the_running_tag(clone):
    evidence = Evidence()
    tools = _tools(
        [CODE], _placement(clone), {"devops-generic": Devops("1.0.0")}, evidence
    )

    said = call(tools["search_code"], repo="reelme", query="Token()")

    assert "src/orders.ts:2:checkToken()" in said
    assert "noToken" not in said
    assert any("checkToken" in line for line in evidence.index.values()), "citable"


def test_search_code_takes_a_dash_query_as_text_not_an_option(clone):
    tools = _tools([CODE], _placement(clone), {})

    said = call(tools["search_code"], repo="reelme", query="--no-index")

    assert "carries '--no-index'" in said


def test_a_repo_that_is_not_the_rooms_is_refused(clone):
    tools = _tools([CODE, DOCS], _placement(clone), {})

    for name, kw in (
        ("read_code", {"file": "src/orders.ts", "line": 1}),
        ("search_code", {"query": "x"}),
        ("what_code_means", {"code": "ERR19"}),
        ("read_docs", {"path": "docs/webhooks.md"}),
    ):
        said = call(tools[name], repo="/etc", **kw)
        assert "is not one of this room's projects" in said, name


# --- docs ----------------------------------------------------------------------


def test_read_docs_reads_under_docs_paths_at_the_running_tag(clone):
    tools = _tools([DOCS], _placement(clone), {"devops-generic": Devops("1.0.0")})

    said = call(tools["read_docs"], repo="reelme", path="docs/webhooks.md")

    assert "signed at 1.0.0" in said and "edited since" not in said


def test_read_docs_lists_what_it_can_read(clone):
    tools = _tools([DOCS], _placement(clone), {})

    assert call(tools["read_docs"], repo="reelme") == "docs/webhooks.md"


def test_a_file_outside_docs_paths_is_not_a_doc(clone):
    tools = _tools([DOCS], _placement(clone), {})

    for path in ("README.md", "src/orders.ts", "docs/../../etc/passwd"):
        assert "is not a document" in call(tools["read_docs"], repo="reelme", path=path)


# --- db --------------------------------------------------------------------------


def test_the_db_tools_read_only_the_rooms_databases():
    class Db:
        async def call_tool(self, tool, arguments):
            return json.dumps({"result": json.dumps({"rows": [{"id": 1}], "count": 1})})

    placement = Placement(env="production", dbs=("payments",))
    tools = _tools([DB], placement, {"db-generic": Db()})

    assert '"id": 1' in call(tools["query_db"], db_id="payments", sql="select 1")
    assert "not one of this room's databases" in call(
        tools["query_db"], db_id="other", sql="select 1"
    )
    assert "does not start a read" in call(
        tools["query_db"], db_id="payments", sql="delete from x"
    )


def test_no_db_server_says_so():
    tools = _tools([DB], Placement(env="production"), {})

    assert "db-generic is not connected" in call(tools["describe_db"], db_id="x")


# --- review fixes ----------------------------------------------------------------


def test_the_tag_is_asked_once_between_code_and_docs(clone):
    """`answer_question` is granted both; the ~105k-char `release_status`
    answer is fetched once per run, not once per toolset."""
    server = Devops("1.0.0")
    tools = _tools([CODE, DOCS], _placement(clone), {"devops-generic": server})

    call(tools["read_code"], repo="reelme", file="src/orders.ts", line=1)
    call(tools["read_docs"], repo="reelme", path="docs/webhooks.md")

    assert server.called == ["release_status"]


def test_a_file_deleted_since_the_release_is_still_read_at_the_tag(clone):
    (clone / "src" / "orders.ts").unlink()
    tools = _tools([CODE], _placement(clone), {"devops-generic": Devops("1.0.0")})

    said = call(tools["read_code"], repo="reelme", file="/app/src/orders.ts", line=1)

    assert "released" in said and "at 1.0.0" in said


def test_a_docs_path_of_the_whole_clone_opens_nothing(clone):
    """`docs_paths: ["."]` would make `.env` and `.git/config` docs."""
    (clone / ".env").write_text("SECRET=1\n")
    placement = Placement(
        env="dev",
        projects=(Project(name="reelme", repo_path=str(clone), docs_paths=(".", "")),),
    )
    tools = _tools([DOCS], placement, {})

    assert "is not a document" in call(tools["read_docs"], repo="reelme", path=".env")
    assert "hold no files" in call(tools["read_docs"], repo="reelme")
