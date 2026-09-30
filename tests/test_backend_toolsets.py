"""build-the-spine ticket 09 — the backend's toolsets, folded out of `sources/`.
Ticket 23 moved reading, searching and listing a repository onto the
generic `core.repos` (`read`/`grep`/`glob`); each takes its own optional
`ref` (a tag, branch or sha) the *model* finds and passes in — there is no
plugin-side running-version lookup (`backend.release`, rejected by the
operator 2026-09-30: the model calls devops tools for the tag itself).

What these guard: a factory reaches only the MCP tools its own toolset
declared (the core narrows every server per toolset); `read`/`grep`/`glob`
read at a given `ref`, never the working copy, and say so when they fall
back to the checkout; `repo` is one of the room's projects and nothing else.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from datetime import UTC, datetime

import pytest

from friday.kernel.harness.harness import ModelRetry
from friday.kernel.harness.run_agent import build_tools, reads_for
from friday.kernel.toolsets.repos import REPOS
from friday.sdk.evidence import Evidence
from friday.sdk.toolset import RunContext
from plugins.backend.placement import Placement, Project
from plugins.backend.toolsets import DB, LOGS, TOOLSETS

AT = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


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
    """Invoke a tool the way the run would: `args_validator` first (ticket
    23's semantic refusals, raised as `ModelRetry`), then the function."""
    if tool.args_validator is not None:
        tool.args_validator(None, **kw)
    result = tool.function(**kw)
    return asyncio.run(result) if inspect.isawaitable(result) else result


# --- a factory reaches only its own declared reads ---------------------------


def test_each_toolset_is_narrowed_to_its_own_reads():
    """Per toolset, not per plugin: `backend.logs` never sees another
    server's reads, and a server a toolset did not declare is absent."""
    servers = {"devops-generic": object(), "db-generic": object()}

    assert reads_for(LOGS, servers)["devops-generic"].allowed == {"loki_query_range"}
    assert set(reads_for(LOGS, servers)) == {"devops-generic"}
    assert set(reads_for(DB, servers)) == {"db-generic"}


def test_the_plugin_registers_every_toolset_under_its_own_name():
    assert [t.name for t in TOOLSETS] == ["backend.logs", "backend.db"]
    assert all(t.domain_type is Placement for t in TOOLSETS)


# --- reading, searching and listing at a ref the model gives (ticket 23) ----


def test_read_reads_at_the_given_ref_not_the_working_copy(clone):
    tools = _tools([REPOS], _placement(clone), {})

    said = call(tools["read"], repo="reelme", path="/app/src/orders.ts", ref="1.0.0")

    assert "released" in said and "edited since" not in said
    assert "at 1.0.0" in said
    head = _git(clone, "rev-parse", "HEAD").stdout
    # `git show`, never a checkout: the operator's tree and HEAD are untouched.
    assert (clone / "src" / "orders.ts").read_text().startswith("edited since")
    assert _git(clone, "rev-parse", "HEAD").stdout == head


def test_no_ref_reads_the_checkout_and_says_so(clone):
    evidence = Evidence()
    tools = _tools([REPOS], _placement(clone), {}, evidence)

    said = call(tools["read"], repo="reelme", path="/app/src/orders.ts")

    assert "edited since" in said
    assert "no ref was given" in said
    assert any("not a pinned ref" in line for line in evidence.not_checked)


def test_a_ref_the_clone_cannot_resolve_is_refused(clone):
    tools = _tools([REPOS], _placement(clone), {})

    with pytest.raises(ModelRetry, match="could not be resolved"):
        call(tools["read"], repo="reelme", path="/app/src/orders.ts", ref="9.9.9")


def test_a_ref_starting_with_a_dash_is_refused(clone):
    tools = _tools([REPOS], _placement(clone), {})

    with pytest.raises(ModelRetry, match="looks like a flag"):
        call(
            tools["read"],
            repo="reelme",
            path="/app/src/orders.ts",
            ref="--upload-pack=x",
        )


def test_a_valid_ref_that_lacks_the_file_falls_back_to_the_checkout(clone):
    (clone / "src" / "new.ts").write_text("brand new\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-qm", "two")
    evidence = Evidence()
    tools = _tools([REPOS], _placement(clone), {}, evidence)

    said = call(tools["read"], repo="reelme", path="/app/src/new.ts", ref="1.0.0")

    assert "brand new" in said
    assert "is not at '1.0.0'" in said
    assert any("not at '1.0.0'" in line for line in evidence.not_checked)


def test_grep_finds_at_the_given_ref(clone):
    evidence = Evidence()
    tools = _tools([REPOS], _placement(clone), {}, evidence)

    said = call(
        tools["grep"],
        repo="reelme",
        pattern="Token\\(\\)",
        ref="1.0.0",
        output_mode="content",
    )

    assert "src/orders.ts:2:checkToken()" in said
    assert "noToken" not in said
    assert any("checkToken" in line for line in evidence.index.values()), "citable"


def test_grep_takes_a_dash_pattern_as_text_not_an_option(clone):
    tools = _tools([REPOS], _placement(clone), {})

    said = call(tools["grep"], repo="reelme", pattern="--no-index")

    assert "Nothing" in said or "carries" in said


def test_a_repo_that_is_not_the_rooms_is_refused(clone):
    tools = _tools([REPOS], _placement(clone), {})

    for name, kw in (
        ("read", {"path": "src/orders.ts"}),
        ("grep", {"pattern": "x"}),
        ("glob", {"pattern": "*"}),
    ):
        with pytest.raises(ModelRetry, match="is not one of this room's projects"):
            call(tools[name], repo="/etc", **kw)


def test_glob_lists_paths_at_the_given_ref(clone):
    tools = _tools([REPOS], _placement(clone), {})

    said = call(tools["glob"], repo="reelme", pattern="**/*.ts", ref="1.0.0")

    assert "src/orders.ts" in said


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


def test_a_file_deleted_since_the_tag_is_still_read_at_it(clone):
    (clone / "src" / "orders.ts").unlink()
    tools = _tools([REPOS], _placement(clone), {})

    said = call(tools["read"], repo="reelme", path="/app/src/orders.ts", ref="1.0.0")

    assert "released" in said and "at 1.0.0" in said
