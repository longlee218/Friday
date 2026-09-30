"""The core toolsets (build-the-spine ticket 08): `core.shell` reads and only
reads, `core.workspace` never leaves `/tmp/friday/<task_id>/`, and one module
imports pydantic-ai-harness.

One test per refusal class of the read-command allowlist, each refused before
anything runs; a refusal is written to `audit_log`.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from friday.kernel.audit import AuditLog
from friday.kernel.toolsets import NotWired, core_toolsets, workspace
from friday.kernel.toolsets.shell import refusal, shell_tools
from friday.sdk.evidence import Evidence
from friday.sdk.redact import clear_secret_values, register_secret_values
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
        mcp={},
        reported_at=datetime(2026, 9, 29, tzinfo=UTC),
    )


def _run_command(db, run=None, hosts=("local",)):
    (tool,) = shell_tools(run or _run(), hosts=hosts, audit=AuditLog(db))
    return tool.function


# ── the allowlist: one test per refusal class ──────────────────────────────


def test_a_command_off_the_list_is_refused():
    assert "not on the read-command allowlist" in refusal("rm -rf /tmp/x")


def test_kubectl_is_refused_outside_its_read_verbs():
    assert "kubectl reads only" in refusal("kubectl delete pod api-0")


@pytest.mark.parametrize(
    "command", ["ls; rm x", "ls && rm x", "ls || rm x", "ls & rm x", "(ls)"]
)
def test_a_control_operator_is_refused(command):
    assert "only `|` between read commands" in refusal(command)


@pytest.mark.parametrize("command", ["cat a > b", "grep x < a", "cat a >> b"])
def test_a_redirection_is_refused(command):
    assert "only `|` between read commands" in refusal(command)


@pytest.mark.parametrize("command", ["cat $(which sh)", "cat `which sh`"])
def test_a_substitution_is_refused(command):
    assert "command substitution" in refusal(command)


async def test_a_quoted_operator_stays_an_argument(db, tmp_path):
    """`grep "|"` searches for a bar; it does not become a pipe."""
    log = tmp_path / "a.log"
    log.write_text("a | b\nplain\n")
    assert refusal(f'grep "|" {log}') is None
    got = await _run_command(db)(host="local", command=f'grep "|" {log}')
    assert "a | b" in got and "plain" not in got
    assert refusal('grep ";" x') is None


def test_a_pipe_is_refused_when_one_segment_is_off_the_list():
    assert "'xargs' is not on" in refusal("ls | xargs rm")
    assert refusal("kubectl get pods -n api | grep Crash | head -5") is None


@pytest.mark.parametrize(
    "command", ["find . -delete", "find . -exec rm {} +", "journalctl --vacuum-time=1s"]
)
def test_a_write_flag_is_refused(command):
    assert "is refused" in refusal(command)


@pytest.mark.parametrize(
    "command",
    [
        "kubectl get pods --kubeconfig=/tmp/friday/7/k.yaml",
        "kubectl get pods --kubeconfig /tmp/friday/7/k.yaml",
        "kubectl get pods --server=https://elsewhere",
        "kubectl get pods -shttps://elsewhere",
        "kubectl logs api-0 --token abc",
        "kubectl get pods --cache-dir=/etc",
        "journalctl --cursor-file=/etc/x",
    ],
)
def test_a_credential_or_write_flag_is_refused(command):
    """`--kubeconfig` names a file whose `exec` entry runs any binary — and
    the workspace can write that file."""
    assert "is refused" in refusal(command)


@pytest.mark.parametrize(
    "command",
    [
        "kubectl get secrets -n api",
        "kubectl get secret/db-creds -o yaml",
        "kubectl get pods,secrets",
        "kubectl describe secret db-creds",
        "kubectl get secrets.v1 -o json",
    ],
)
def test_the_secret_resource_is_refused(command):
    assert "secret resource" in refusal(command)
    assert refusal("kubectl get pods -o wide") is None


@pytest.mark.parametrize(
    "command",
    [
        "cat /srv/app/.env",
        "cat /srv/app/.env.production",
        "head /Users/op/.ssh/id_ed25519",
        "cat /etc/nginx/tls/server.key",
        "cat /home/op/.aws/credentials",
        "grep --file=/srv/app/.env x /srv/app/log",
        "tail /home/op/.config/gcloud/credentials.db",
    ],
)
def test_a_credential_path_is_refused(command):
    assert "credential file or directory" in refusal(command)


@pytest.mark.parametrize(
    "command",
    [
        # kubectl around the resource check
        "kubectl get --raw /api/v1/namespaces/default/secrets/db",
        "kubectl get --raw=/api/v1/secrets",
        "kubectl get -f /tmp/friday/7/s.yaml -o yaml",
        "kubectl get --filename=/tmp/friday/7/s.yaml",
        "kubectl get -k /tmp/friday/7/kust",
        # grep around its excludes
        "grep -r --include=* x /home/u",
        "grep -R x /home/u/link",
        "grep -nR x /home/u/link",
        "grep --dereference-recursive x /home/u",
        "grep -r x /home/u/gcloud",
        # names the first lists missed, and case
        "cat /Users/u/.SSH/ID_RSA",
        "cat /var/run/secrets/kubernetes.io/serviceaccount/token",
        "cat /etc/kubernetes/admin.conf",
        "cat /etc/ssh/ssh_host_ed25519_key",
        "cat /app/prod.env",
        "cat /home/u/.npmrc",
        "cat /infra/terraform.tfstate",
        "cat /proc/1234/environ",
        # process environments
        "ps eww",
        "ps auxe",
        "ps -E",
        # the second review's
        "grep -ie. /root/.aws/credentials",
        "grep --regex=. /root/.aws/credentials",
        "grep --inc=* -r x /home",
        "kubectl get -Af m.yaml",
        "kubectl get -Rk dir",
        "kubectl get pods -o jsonpath-file=/root/.ssh/id_rsa",
        "kubectl get pods -o=go-template-file=/app/.env",
        "kubectl get pods --template={{.x}}",
        "cat /etc/kubernetes/super-admin.conf",
    ],
)
def test_a_reported_bypass_is_refused(command):
    assert refusal(command) is not None, command


@pytest.mark.parametrize(
    "command",
    [
        "grep -c KEY /srv/app/.env.example",
        "grep -A 2 timeout /var/log/app.log",
        "ps -o user",
        "ps -u deploy",
        "ps -C sleep",
        "find . -name *.env",
        "ls /home/u/.ssh",
        "cat /etc/pki/tls/certs/ca-bundle.crt",
        "grep -rn timeout /srv/app/config",
        "ps aux",
        "ps -ef",
        "kubectl logs api-0 --follow",
        "kubectl get pods -l app=api -o wide",
    ],
)
def test_ordinary_debugging_still_passes(command):
    assert refusal(command) is None, refusal(command)


@pytest.mark.parametrize(
    "command",
    [
        "grep id_rsa /var/log/auth.log",
        'grep "\\.pem" /etc/nginx/nginx.conf',
        "grep -A 2 id_rsa /var/log/auth.log",
        "grep -iA2 id_rsa /var/log/auth.log",
        "grep -iA 2 id_rsa /var/log/auth.log",
        "grep --max-count 5 id_rsa /var/log/auth.log",
        "grep --files-with-matches id_rsa /var/log",
        "grep -- .env /srv/app/README.md",
        "kubectl get pods -n secrets",
        "kubectl get pods --namespace secrets -o wide",
        "kubectl get pods -A -n secrets --no-headers",
        "kubectl get pods --namespace=secrets -n secrets",
        "kubectl logs -f api-0",
        "kubectl logs -fp api-0",
        "kubectl get -ojsonpath={.status.phase} pods",
        "kubectl get pods -lapp=frontend",
    ],
)
def test_the_four_false_refusals_are_allowed(command):
    """grep's pattern is a search term; `-n secrets` a namespace; `logs -f`
    is --follow; a value written into a kubectl cluster is that value."""
    assert refusal(command) is None, refusal(command)


@pytest.mark.parametrize(
    "command",
    [
        # the pattern comes from an option: every word is a file
        "grep -e x /srv/app/.env",
        "grep -ie. /root/.aws/credentials",
        "grep -e. -i /root/.aws/credentials",
        "grep --regex=. /root/.aws/credentials",
        "grep --reg . /root/.aws/credentials",
        "grep -if/tmp/p /root/.aws/credentials",
        "grep --fil=/tmp/p /root/.aws/credentials",
        # only the first word after the options is the pattern
        "grep id_rsa /home/u/.ssh/id_rsa",
        "grep -A 2 x /root/.env",
        "grep -m 5 x /root/.env",
        "grep x -r /home/u/.ssh",
        # the namespace skip and the cluster walk do not open the resource
        "kubectl get -n default secrets",
        "kubectl get secrets -n default",
        "kubectl get -An secrets",
        "kubectl get -Af m.yaml",
        "kubectl get -Rk dir",
        "kubectl logs -fs https://elsewhere api-0",
        "kubectl get pods -f m.yaml",
        # the third review's: a misparse moved the pattern onto the file
        "grep --binary root /home/u/.ssh/id_rsa",
        "grep --context root /home/u/.ssh/id_rsa",
        "grep -C root /home/u/.ssh/id_rsa",
        "grep --line-num x /root/.env",
        "grep -iy x /root/.env",
        "grep --color=always x /root/.env",
        "grep -A /root/.env x",
        "kubectl get -L -L secrets -o yaml",
        "kubectl get -l -n secrets",
        "kubectl get --label-columns -n secrets",
        "kubectl get --profile-output -n secrets",
        "kubectl describe --tls-server-name -n secrets",
    ],
)
def test_the_allowances_open_no_bypass(command):
    assert refusal(command) is not None, command


async def test_a_recursive_grep_skips_credential_files(db, tmp_path):
    """`grep -r` names only the directory, so the per-argument check cannot
    see the `.env` beneath it; the excludes do."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / ".env").write_text("MARK=hunter2\n")
    (tmp_path / "app" / ".ssh").mkdir()
    (tmp_path / "app" / ".ssh" / "config").write_text("MARK in ssh\n")
    (tmp_path / "app" / "main.log").write_text("MARK used\n")

    got = await _run_command(db)(
        host="local", command=f"grep -r MARK {tmp_path / 'app'}"
    )

    assert "MARK used" in got
    assert "hunter2" not in got and "in ssh" not in got


@pytest.mark.parametrize("command", ["", "  ", "ls |", "| ls", "cat 'unclosed"])
def test_an_empty_or_unparseable_command_is_refused(command):
    assert refusal(command) is not None


# ── the tool ────────────────────────────────────────────────────────────────


async def test_a_refused_command_writes_an_audit_row_and_runs_nothing(db, tmp_path):
    marker = tmp_path / "marker"
    got = await _run_command(db)(host="local", command=f"ls; touch {marker}")

    assert got.startswith("refused:")
    assert not marker.exists()
    (entry,) = await db.audit_entries(event="shell_refused")
    assert entry.detail["task_id"] == 7
    assert entry.detail["toolset"] == "core.shell"
    assert entry.detail["host"] == "local"
    assert entry.detail["command"] == f"ls; touch {marker}"


async def test_an_undeclared_host_is_refused_and_audited(db):
    got = await _run_command(db, hosts=("local",))(host="prod", command="ls")

    assert "host 'prod' is not declared" in got
    (entry,) = await db.audit_entries(event="shell_refused")
    assert entry.detail["host"] == "prod"


async def test_output_is_redacted_and_enters_evidence(db, tmp_path):
    log = tmp_path / "app.log"
    log.write_text("ok start\nfailed with key s3cr3t-value\nok end\n")
    run = _run()
    register_secret_values(["s3cr3t-value"])
    try:
        got = await _run_command(db, run)(
            host="local", command=f"cat {log} | grep failed"
        )
    finally:
        clear_secret_values()

    assert got.startswith("exit 0")
    assert "s3cr3t-value" not in got
    assert run.evidence.index == {"L1": "failed with key [REDACTED]"}
    assert "L1 | failed with key [REDACTED]" in got


async def test_an_argument_reaches_the_shell_as_a_literal(db, tmp_path):
    """Re-quoted before `sh -c`: a `$HOME` in an argument is not expanded,
    even in the double quotes a shell would expand it in."""
    log = tmp_path / "a.log"
    log.write_text("price $HOME\n")
    got = await _run_command(db)(host="local", command=f'grep "$HOME" {log}')
    assert "price $HOME" in got


async def test_save_to_writes_the_output_into_the_workspace(db, tmp_path, root):
    log = tmp_path / "big.log"
    log.write_text("".join(f"line {n}\n" for n in range(500)))
    run = _run()
    got = await _run_command(db, run)(
        host="local", command=f"cat {log}", save_to="out/big.txt"
    )

    assert "500 lines" in got and "saved to out/big.txt" in got
    assert (root / "7" / "out" / "big.txt").read_text() == log.read_text()
    assert run.evidence.index == {}


async def test_a_timeout_kills_the_whole_pipeline(db, tmp_path, monkeypatch):
    """`tail -f | grep` past the timeout: no process of the pipeline survives."""
    import subprocess

    from friday.kernel.toolsets import shell

    monkeypatch.setattr(shell, "TIMEOUT_SECONDS", 0.5)
    log = tmp_path / "follow-me.log"
    log.write_text("x\n")
    # Bounded here too: a pipeline child left holding the pipe would otherwise
    # hang the test rather than fail it.
    got = await asyncio.wait_for(
        _run_command(db)(host="local", command=f"tail -f {log} | grep x"), 5
    )

    assert "stopped after" in got
    alive = subprocess.run(
        ["pgrep", "-f", str(log)], capture_output=True, text=True, check=False
    )
    assert alive.stdout.strip() == ""


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
    assert {"core.shell", "core.workspace"} <= set(toolsets)
    assert loaded.registry.owner_of("core.shell").id == "core"
