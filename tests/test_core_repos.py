"""`core.repos` (build-the-spine ticket 23): `read`, `grep`, `glob` over a
domain's `RepoRoom`, generic in the kernel.

What these guard, beyond `tests/test_investigate_tools.py` and
`tests/test_backend_toolsets.py`'s running-tag/refusal coverage: a secret
file or directory is never read, searched or listed; an oversize read is
refused rather than truncated; each tool's description is rendered from the
constants it enforces; the three tools build the same definition whether or
not their function carries a docstring; and `backend.diagnose`/
`backend.explain`, run through `run_agent`, are offered `read`/`grep`/`glob`
and none of the tools ticket 23 deleted.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime

import pytest

from friday.kernel.harness.harness import ModelRetry, _bind_tool_spec
from friday.kernel.toolsets.repos import (
    DEFAULT_HEAD_LIMIT,
    DEFAULT_READ_LINES,
    MAX_GLOB_RESULTS,
    MAX_GREP_CHARS,
    MAX_GREP_LINE_CHARS,
    MAX_READ_BYTES,
    MAX_READ_TOKENS,
    _glob_description,
    _grep_description,
    _read_description,
    repos_tools,
)
from friday.sdk.evidence import Evidence
from friday.sdk.toolset import RunContext
from plugins.backend.placement import Placement, Project

AT = datetime(2026, 9, 30, tzinfo=UTC)


def _git_init(root) -> None:
    """`grep`'s working-tree search is `git grep`, which needs a repository
    even with no ref — unlike `read`/`glob`'s checkout fallback, which reads
    the filesystem directly."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        cwd=root,
        check=True,
    )


def built(*, repo_path: str) -> dict:
    run = RunContext(
        task_id=1,
        domain=Placement(env="dev", projects=(Project(name="r", repo_path=repo_path),)),
        evidence=Evidence(),
        mcp={},
        reported_at=AT,
    )
    return {t.fn.__name__: _bind_tool_spec(t) for t in repos_tools(run)}


def call(tool, **kw):
    if tool.args_validator is not None:
        tool.args_validator(None, **kw)
    result = tool.function(**kw)
    return asyncio.run(result) if inspect.isawaitable(result) else result


# --- secret files and directories are never reached --------------------------


def test_a_secret_file_is_refused_before_it_is_read(tmp_path):
    (tmp_path / ".env").write_text("SECRET=1\n")
    tools = built(repo_path=str(tmp_path))

    with pytest.raises(ModelRetry, match="credential file or directory"):
        call(tools["read"], repo="r", path=".env")


def test_a_path_under_a_secret_directory_is_refused(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("not yours\n")
    tools = built(repo_path=str(tmp_path))

    with pytest.raises(ModelRetry, match="credential file or directory"):
        call(tools["read"], repo="r", path=".ssh/id_rsa")


def test_grep_excludes_secret_files_even_with_no_glob(tmp_path):
    (tmp_path / ".env").write_text("MARK=hunter2\n")
    (tmp_path / "main.log").write_text("MARK used\n")
    _git_init(tmp_path)
    tools = built(repo_path=str(tmp_path))

    said = call(tools["grep"], repo="r", pattern="MARK", output_mode="content")

    assert "used" in said
    assert "hunter2" not in said


def test_glob_never_lists_a_secret_file(tmp_path):
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / "main.py").write_text("x = 1\n")
    tools = built(repo_path=str(tmp_path))

    said = call(tools["glob"], repo="r", pattern="**/*")

    assert "main.py" in said
    assert ".env" not in said


# --- an oversize read is refused, not truncated -------------------------------


def test_a_file_over_the_token_estimate_is_refused_without_limit(tmp_path):
    (tmp_path / "big.txt").write_text("x" * (MAX_READ_TOKENS * 4 + 100))
    tools = built(repo_path=str(tmp_path))

    with pytest.raises(ModelRetry, match="offset/limit"):
        call(tools["read"], repo="r", path="big.txt")


def test_a_file_over_the_token_estimate_reads_with_limit(tmp_path):
    (tmp_path / "big.txt").write_text("\n".join(str(i) for i in range(20_000)))
    tools = built(repo_path=str(tmp_path))

    said = call(tools["read"], repo="r", path="big.txt", offset=1, limit=5)

    assert "0" in said and "4" in said


def test_a_directory_is_refused_with_a_hint(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("x\n")
    tools = built(repo_path=str(tmp_path))

    with pytest.raises(ModelRetry, match="grep or glob"):
        call(tools["read"], repo="r", path="src")


def test_a_binary_file_is_refused(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"\x00\x01\x02binary")
    tools = built(repo_path=str(tmp_path))

    with pytest.raises(ModelRetry, match="binary"):
        call(tools["read"], repo="r", path="a.bin")


# --- unchanged-range dedup -----------------------------------------------------


def test_reading_the_same_range_twice_gets_a_stub_not_the_text_again(tmp_path):
    (tmp_path / "a.ts").write_text("one\ntwo\nthree\n")
    tools = built(repo_path=str(tmp_path))

    first = call(tools["read"], repo="r", path="a.ts")
    second = call(tools["read"], repo="r", path="a.ts")

    assert "two" in first
    assert "unchanged since last read" in second
    assert "two" not in second


def test_an_edited_checkout_file_is_not_served_the_stale_stub(tmp_path):
    target = tmp_path / "a.ts"
    target.write_text("one\n")
    tools = built(repo_path=str(tmp_path))

    call(tools["read"], repo="r", path="a.ts")
    target.write_text("changed\n")
    second = call(tools["read"], repo="r", path="a.ts")

    assert "changed" in second
    assert "unchanged" not in second


# --- grep's output modes -------------------------------------------------------


def test_grep_files_with_matches_is_sorted_and_counted(tmp_path):
    (tmp_path / "b.py").write_text("needle\n")
    (tmp_path / "a.py").write_text("needle\n")
    (tmp_path / "c.py").write_text("nothing here\n")
    _git_init(tmp_path)
    tools = built(repo_path=str(tmp_path))

    said = call(tools["grep"], repo="r", pattern="needle")

    assert "Found 2 files" in said
    assert said.index("a.py") < said.index("b.py")
    assert "c.py" not in said


def test_grep_count_mode_reports_per_file_counts(tmp_path):
    (tmp_path / "a.py").write_text("needle\nneedle\n")
    _git_init(tmp_path)
    tools = built(repo_path=str(tmp_path))

    said = call(tools["grep"], repo="r", pattern="needle", output_mode="count")

    assert "a.py:2" in said


def test_grep_head_limit_and_offset_window_the_files(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.py").write_text("needle\n")
    _git_init(tmp_path)
    tools = built(repo_path=str(tmp_path))

    said = call(tools["grep"], repo="r", pattern="needle", head_limit=1, offset=1)

    assert "b.py" in said
    assert "a.py" not in said and "c.py" not in said


def test_grep_glob_narrows_the_search(tmp_path):
    (tmp_path / "a.py").write_text("needle\n")
    (tmp_path / "a.md").write_text("needle\n")
    _git_init(tmp_path)
    tools = built(repo_path=str(tmp_path))

    said = call(tools["grep"], repo="r", pattern="needle", glob="*.py")

    assert "a.py" in said and "a.md" not in said


# --- glob's cap ----------------------------------------------------------------


def test_glob_caps_at_100_and_says_so(tmp_path):
    for i in range(105):
        (tmp_path / f"f{i:03}.txt").write_text("x")
    tools = built(repo_path=str(tmp_path))

    said = call(tools["glob"], repo="r", pattern="*.txt")

    assert said.count(".txt") == MAX_GLOB_RESULTS


def test_glob_reports_a_git_failure_at_a_resolved_ref_rather_than_no_matches(
    tmp_path, monkeypatch
):
    """A `ref` that resolves (`_ref_resolves` already checked it) but whose
    `ls-tree` fails for some other reason is a git-level failure, not the
    same answer as a true zero-match glob."""
    import subprocess as subprocess_module

    from friday.kernel.toolsets import repos as repos_module

    (tmp_path / "a.py").write_text("x\n")
    _git_init(tmp_path)
    subprocess_module.run(["git", "tag", "1.0.0"], cwd=tmp_path, check=True)
    tools = built(repo_path=str(tmp_path))

    real_run = subprocess_module.run

    def flaky_run(args, **kwargs):
        if "ls-tree" in args:
            return subprocess_module.CompletedProcess(
                args, 128, stdout="", stderr="fatal: boom"
            )
        return real_run(args, **kwargs)

    monkeypatch.setattr(repos_module.subprocess, "run", flaky_run)

    said = call(tools["glob"], repo="r", pattern="*.py", ref="1.0.0")

    assert "could not be listed" in said
    assert "Nothing in r matches" not in said


# --- descriptions are rendered from the constants they enforce ---------------


def test_each_descriptions_numbers_are_the_ones_it_enforces():
    read_said, grep_said, glob_said = (
        _read_description(),
        _grep_description(),
        _glob_description(),
    )

    assert f"{MAX_READ_BYTES // 1024} KB" in read_said
    assert f"{MAX_READ_TOKENS} tokens" in read_said
    assert f"{DEFAULT_READ_LINES} lines" in read_said

    assert f"{DEFAULT_HEAD_LIMIT} results" in grep_said
    assert f"{MAX_GREP_LINE_CHARS} characters" in grep_said
    assert f"{MAX_GREP_CHARS}" in grep_said

    assert f"{MAX_GLOB_RESULTS} results" in glob_said


# --- declared explicitly, never from a docstring (ticket 23's own rule) ------


def test_the_three_tools_are_the_same_definition_whether_or_not_they_have_a_docstring(
    tmp_path,
):
    run = RunContext(
        task_id=1,
        domain=Placement(
            env="dev", projects=(Project(name="r", repo_path=str(tmp_path)),)
        ),
        evidence=Evidence(),
        mcp={},
        reported_at=AT,
    )
    with_docstrings = {t.fn.__name__: _bind_tool_spec(t) for t in repos_tools(run)}

    stripped_specs = repos_tools(run)
    for spec in stripped_specs:
        spec.fn.__doc__ = None
    without_docstrings = {t.fn.__name__: _bind_tool_spec(t) for t in stripped_specs}

    assert set(with_docstrings) == {"read", "grep", "glob"}
    for name, with_doc in with_docstrings.items():
        without_doc = without_docstrings[name]
        assert with_doc.description == without_doc.description
        assert (
            with_doc.function_schema.json_schema
            == without_doc.function_schema.json_schema
        )


# --- the agents that grant core.repos are offered it, and nothing deleted ----


async def test_backend_diagnose_is_offered_read_grep_glob():
    from friday.kernel.config import TierConfig
    from friday.kernel.harness.run_agent import run_agent
    from friday.sdk.testing import FunctionModel, ModelResponse, function_call
    from plugins.backend.actions.trace_problem import ACTION
    from plugins.backend.agents.diagnose import DIAGNOSE
    from plugins.backend.toolsets import LOGS

    offered: list[str] = []

    def reply(messages, info):
        offered.extend(t.name for t in info.function_tools)
        return ModelResponse(parts=[function_call("hand_over", {"reason": "x"})])

    from friday.kernel.toolsets.repos import REPOS

    await run_agent(
        DIAGNOSE,
        TierConfig(name="flash", api_key="k", base_url="https://x.invalid", model="m"),
        ACTION.contract,
        [LOGS, REPOS],
        RunContext(
            task_id=1,
            domain=Placement(env="dev"),
            evidence=Evidence(),
            mcp={},
            reported_at=AT,
        ),
        "look",
        None,
        model=FunctionModel(reply, model_name="m"),
    )

    assert {"read", "grep", "glob", "read_log"} <= set(offered)
    assert not {"read_code", "search_code", "what_code_means", "read_docs"} & set(
        offered
    )


async def test_backend_explain_is_offered_read_grep_glob():
    from friday.kernel.config import TierConfig
    from friday.kernel.harness.run_agent import run_agent
    from friday.kernel.toolsets.repos import REPOS
    from friday.sdk.testing import FunctionModel, ModelResponse, function_call
    from plugins.backend.actions.answer_question import ACTION
    from plugins.backend.agents.explain import EXPLAIN

    offered: list[str] = []

    def reply(messages, info):
        offered.extend(t.name for t in info.function_tools)
        return ModelResponse(parts=[function_call("hand_over", {"reason": "x"})])

    await run_agent(
        EXPLAIN,
        TierConfig(name="flash", api_key="k", base_url="https://x.invalid", model="m"),
        ACTION.contract,
        [REPOS],
        RunContext(
            task_id=1,
            domain=Placement(env="dev"),
            evidence=Evidence(),
            mcp={},
            reported_at=AT,
        ),
        "look",
        None,
        model=FunctionModel(reply, model_name="m"),
    )

    assert {"read", "grep", "glob"} <= set(offered)
    assert not {"read_code", "search_code", "what_code_means", "read_docs"} & set(
        offered
    )
