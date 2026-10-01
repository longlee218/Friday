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
from plugins.backend.toolsets import DB, K8S, LOGS, RELEASE_STATUS, TOOLSETS

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
    assert reads_for(RELEASE_STATUS, servers)["devops-generic"].allowed == {
        "release_status"
    }
    assert reads_for(K8S, servers)["devops-generic"].allowed == {"k8s_pod_status"}


def test_the_plugin_registers_every_toolset_under_its_own_name():
    assert [t.name for t in TOOLSETS] == [
        "backend.logs",
        "backend.db",
        "backend.release_status",
        "backend.k8s",
    ]
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


# --- release_status / k8s (ticket 28's minimal slice) -----------------------


class _ScriptedDevops:
    """A `devops-generic` stand-in that records every call and answers with
    whatever the test hands it — a dict is serialized, a string (a captured
    fixture, already JSON) is returned as-is."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, tool, arguments):
        self.calls.append((tool, arguments))
        return (
            self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        )


#: Captured from `devops-generic` on 2026-09-30: `cluster=oregon-llm,
#: namespace=vsl, pod=backend-reelme-v2-856bb78b6c-8qhsv`. The raw response
#: is the pod's own Kubernetes object (`metadata` + `status`, no wrapper);
#: trimmed here to drop `managedFields`, annotations (one of them a Vault
#: secret template) and `ownerReferences`. Running `…/backend-reelme-v2:0.4.9`
#: with 9 restarts and a `lastState.terminated` of `OOMKilled`/exit 137 —
#: `release_status` for the same service, the same day, said `0.4.8`.
K8S_POD_STATUS_ANSWER = """{"metadata":{"name":"backend-reelme-v2-856bb78b6c-8qhsv",
"namespace":"vsl","creationTimestamp":"2026-09-30T05:39:00Z",
"labels":{"app":"backend-reelme-v2"}},
"status":{"phase":"Running","conditions":[
 {"type":"PodReadyToStartContainers","status":"True"},
 {"type":"Initialized","status":"True"},
 {"type":"Ready","status":"True"},
 {"type":"ContainersReady","status":"True"},
 {"type":"PodScheduled","status":"True"}],
"hostIP":"10.15.163.40","podIP":"10.15.169.98","startTime":"2026-09-30T05:39:00Z",
"initContainerStatuses":[
 {"name":"vault-agent-init",
  "state":{"terminated":{"exitCode":0,"reason":"Completed",
                          "startedAt":"2026-09-30T05:39:00Z",
                          "finishedAt":"2026-09-30T05:39:01Z"}},
  "lastState":{},"ready":true,"restartCount":0,
  "image":"docker.io/hashicorp/vault:1.21"}],
"containerStatuses":[
 {"name":"backend-reelme-v2",
  "state":{"running":{"startedAt":"2026-09-30T11:28:17Z"}},
  "lastState":{"terminated":{"exitCode":137,"reason":"OOMKilled",
                              "startedAt":"2026-09-30T11:22:10Z",
                              "finishedAt":"2026-09-30T11:26:47Z"}},
  "ready":true,"restartCount":9,
  "image":"803614452193.dkr.ecr.ap-southeast-1.amazonaws.com/backend-reelme-v2:0.4.9"}],
"qosClass":"Burstable"}}"""


def test_release_status_forwards_its_arguments_unchanged():
    server = _ScriptedDevops(
        {
            "status": {
                "config": {
                    "image": {"repository": "r/backend-reelme-v2", "tag": "0.4.8"}
                },
                "info": {"status": "deployed", "last_deployed": "2026-09-21T00:00:00Z"},
            },
            "history": [],
        }
    )
    tools = _tools(
        [RELEASE_STATUS], Placement(env="production"), {"devops-generic": server}
    )

    said = call(tools["release_status"], project="backend-reelme-v2", env="prod")

    assert server.calls == [
        ("release_status", {"project": "backend-reelme-v2", "env": "prod"})
    ]
    assert "0.4.8" in said
    assert "{" not in said
    assert said.startswith("L1 |")


def test_release_status_dev_empty_reads_as_no_release_not_an_error():
    server = _ScriptedDevops({"status": {}, "history": []})
    tools = _tools(
        [RELEASE_STATUS], Placement(env="production"), {"devops-generic": server}
    )

    said = call(tools["release_status"], project="backend-reelme-v2", env="dev")

    assert "no release found in dev" in said


def test_release_status_an_explicit_null_config_reads_as_no_release_not_a_crash():
    """`status.get("config", {})` only substitutes the default when the key
    is *absent* — an explicit JSON `null` (`{"config": null}`) is a stored
    value the default never replaces, and `.get("image")` on it raised
    `AttributeError` (code review finding). `info`'s neighboring line already
    used `or {}` for the same reason; `config`'s line now matches it."""
    server = _ScriptedDevops({"status": {"config": None, "info": {}}, "history": []})
    tools = _tools(
        [RELEASE_STATUS], Placement(env="production"), {"devops-generic": server}
    )

    said = call(tools["release_status"], project="p", env="prod")

    assert "no release found in prod" in said


def test_release_status_caps_history_and_says_so():
    history = [
        {
            "revision": i,
            "status": "superseded",
            "chart": "c",
            "app_version": "a",
            "updated": "t",
            "description": "d",
        }
        for i in range(1, 11)
    ]
    server = _ScriptedDevops(
        {
            "status": {
                "config": {"image": {"repository": "r", "tag": "0.4.8"}},
                "info": {"status": "deployed", "last_deployed": "t"},
            },
            "history": history,
        }
    )
    tools = _tools(
        [RELEASE_STATUS], Placement(env="production"), {"devops-generic": server}
    )

    said = call(tools["release_status"], project="p", env="prod")

    assert "5 older revisions not shown" in said


def test_no_release_status_server_says_so():
    tools = _tools([RELEASE_STATUS], Placement(env="production"), {})

    assert "devops-generic is not connected" in call(
        tools["release_status"], project="p", env="prod"
    )


def test_k8s_pod_status_forwards_its_arguments_unchanged():
    server = _ScriptedDevops(K8S_POD_STATUS_ANSWER)
    tools = _tools([K8S], Placement(env="production"), {"devops-generic": server})

    said = call(
        tools["k8s_pod_status"],
        cluster="oregon-llm",
        namespace="vsl",
        pod="backend-reelme-v2-856bb78b6c-8qhsv",
    )

    assert server.calls == [
        (
            "k8s_pod_status",
            {
                "cluster": "oregon-llm",
                "namespace": "vsl",
                "pod": "backend-reelme-v2-856bb78b6c-8qhsv",
            },
        )
    ]
    assert "0.4.9" in said
    assert "{" not in said


def test_k8s_pod_status_renders_restart_history_and_init_containers():
    """The fields the operator called out by name: a restarted container's
    `lastState.terminated` (`OOMKilled`, exit 137) and an init container,
    rendered shorter than the main one."""
    server = _ScriptedDevops(K8S_POD_STATUS_ANSWER)
    tools = _tools([K8S], Placement(env="production"), {"devops-generic": server})

    said = call(
        tools["k8s_pod_status"],
        cluster="oregon-llm",
        namespace="vsl",
        pod="backend-reelme-v2-856bb78b6c-8qhsv",
    )

    assert "restarts 9" in said
    assert "OOMKilled" in said and "exit 137" in said
    init_line = next(line for line in said.splitlines() if "vault-agent-init" in line)
    assert "restarts" not in init_line, (
        "init containers render shorter: no ready/restarts"
    )


def test_k8s_pod_status_lists_only_unhealthy_conditions():
    server = _ScriptedDevops(K8S_POD_STATUS_ANSWER)
    tools = _tools([K8S], Placement(env="production"), {"devops-generic": server})

    said = call(
        tools["k8s_pod_status"],
        cluster="oregon-llm",
        namespace="vsl",
        pod="backend-reelme-v2-856bb78b6c-8qhsv",
    )

    assert "5/5 healthy" in said
    assert not any(
        line.split(" | ", 1)[-1].startswith("condition ") for line in said.splitlines()
    )


def test_no_k8s_server_says_so():
    tools = _tools([K8S], Placement(env="production"), {})

    assert "devops-generic is not connected" in call(
        tools["k8s_pod_status"], cluster="c", namespace="n", pod="p"
    )


def test_release_status_and_k8s_pod_status_can_genuinely_disagree():
    """Measured 2026-09-30: `release_status` said `backend-reelme-v2` was on
    `0.4.8` in prod; the pod it was actually running was `0.4.9`. Both
    sources render their own answer — the model is told to prefer the pod's."""
    placement = Placement(env="production", service="backend-reelme-v2")
    helm = _ScriptedDevops(
        {
            "status": {
                "config": {
                    "image": {"repository": "r/backend-reelme-v2", "tag": "0.4.8"}
                },
                "info": {"status": "deployed", "last_deployed": "t"},
            },
            "history": [],
        }
    )
    pod = _ScriptedDevops(K8S_POD_STATUS_ANSWER)
    release_tools = _tools([RELEASE_STATUS], placement, {"devops-generic": helm})
    k8s_tools = _tools([K8S], placement, {"devops-generic": pod})

    helm_said = call(
        release_tools["release_status"], project="backend-reelme-v2", env="prod"
    )
    pod_said = call(
        k8s_tools["k8s_pod_status"],
        cluster="oregon-llm",
        namespace="vsl",
        pod="backend-reelme-v2-856bb78b6c-8qhsv",
    )

    assert "0.4.8" in helm_said
    assert "0.4.9" in pod_said


def test_release_status_and_k8s_pod_status_are_the_same_definition_without_a_docstring():
    from friday.kernel.harness.harness import _bind_tool_spec
    from plugins.backend.toolsets.k8s.pod_status import k8s_tools
    from plugins.backend.toolsets.release_status import release_status_tools

    run = RunContext(
        task_id=1,
        domain=Placement(env="production", service="s"),
        evidence=Evidence(),
        mcp={},
        reported_at=AT,
    )
    for factory in (release_status_tools, k8s_tools):
        (with_doc_spec,) = factory(run)
        with_doc = _bind_tool_spec(with_doc_spec)
        (stripped_spec,) = factory(run)
        stripped_spec.fn.__doc__ = None
        without_doc = _bind_tool_spec(stripped_spec)
        assert with_doc.description == without_doc.description
        assert (
            with_doc.function_schema.json_schema
            == without_doc.function_schema.json_schema
        )


# --- review fixes ----------------------------------------------------------------


def test_a_file_deleted_since_the_tag_is_still_read_at_it(clone):
    (clone / "src" / "orders.ts").unlink()
    tools = _tools([REPOS], _placement(clone), {})

    said = call(tools["read"], repo="reelme", path="/app/src/orders.ts", ref="1.0.0")

    assert "released" in said and "at 1.0.0" in said
