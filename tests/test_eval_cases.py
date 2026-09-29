"""Turn cases on disk (`friday.kernel.evals.cases`): one markdown file per
case, one folder per label, long messages under `_messages/`.

Written by hand and reviewed as a diff, so every malformed file is refused
with its path — a case the operator believes is scored and silently is not is
worse than a run that says which file is wrong.
"""

from __future__ import annotations

import pytest

from friday.kernel.evals.cases import CaseError, load_turn_cases, write_turn_case


def _write(root, rel, content):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_a_body_is_one_message_and_the_folder_is_the_label(tmp_path):
    _write(
        tmp_path,
        "skip/001-lunch.md",
        "---\nexpected_task: skip\n---\n\nanyone want lunch\n",
    )

    (case,) = load_turn_cases(tmp_path)

    assert case.name == "skip/001-lunch"
    assert case.expected == "skip"
    assert case.inputs == (("anyone want lunch", False),)


def test_a_long_body_keeps_its_lines_and_code_blocks(tmp_path):
    body = "a check giúp e:\n\n```\ncurl -X POST https://x/v1/orders \\\n  -d '{}'\n```"
    _write(
        tmp_path,
        "backend.trace_problem/001-curl.md",
        f"---\nexpected_task: backend.trace_problem\n---\n\n{body}\n",
    )

    (case,) = load_turn_cases(tmp_path)

    assert case.inputs == ((body, False),)


def test_a_turn_reads_text_files_and_the_own_mark_in_order(tmp_path):
    _write(tmp_path, "_messages/ticket.md", "# P0\n\n---\n\nđơn không tới Printful\n")
    _write(
        tmp_path,
        "backend.trace_problem/001-turn.md",
        (
            "---\nexpected_task: backend.trace_problem\nturn:\n"
            '  - text: "Cứu ơi"\n'
            "  - file: _messages/ticket.md\n"
            '  - text: "check xem nhé"\n    own: true\n---\n'
        ),
    )

    (case,) = load_turn_cases(tmp_path)

    assert case.inputs == (
        ("Cứu ơi", False),
        ("# P0\n\n---\n\nđơn không tới Printful", False),
        ("check xem nhé", True),
    )


def test_messages_are_not_cases_and_cases_come_back_sorted(tmp_path):
    _write(tmp_path, "_messages/x.md", "just a message\n")
    _write(tmp_path, "skip/002-b.md", "---\nexpected_task: skip\n---\nb\n")
    _write(tmp_path, "skip/001-a.md", "---\nexpected_task: skip\n---\na\n")

    assert [c.name for c in load_turn_cases(tmp_path)] == ["skip/001-a", "skip/002-b"]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("anyone want lunch\n", "no `---` frontmatter"),
        ("---\nexpected_task: backend.trace_problem\n---\nx\n", "must agree"),
        ("---\nexpected_task: skip\nlabel: skip\n---\nx\n", "may hold only"),
        ("---\nexpected_task: skip\n---\n\n", "no message"),
        ("---\nexpected_task: skip\nturn: []\n---\n", "`turn` is empty"),
        (
            "---\nexpected_task: skip\nturn:\n  - text: a\n---\nbody too\n",
            "has no body",
        ),
        (
            "---\nexpected_task: skip\nturn:\n  - text: a\n    file: b\n---\n",
            "`text:` or `file:`",
        ),
        (
            "---\nexpected_task: skip\nturn:\n  - file: _messages/none.md\n---\n",
            "is not a file",
        ),
        (
            "---\nexpected_task: skip\nturn:\n  - file: ../../etc/passwd\n---\n",
            "is not a file",
        ),
        (
            "---\nexpected_task: skip\nturn:\n  - file: _messages/empty.md\n---\n",
            "is empty",
        ),
        ('---\nexpected_task: skip\nturn:\n  - text: "  "\n---\n', "empty `text`"),
        ("---\nexpected_task: [skip\n---\nx\n", "not valid YAML"),
    ],
)
def test_a_malformed_case_is_refused_with_its_path(tmp_path, content, reason):
    _write(tmp_path, "_messages/empty.md", "")
    _write(tmp_path, "skip/001-bad.md", content)

    with pytest.raises(CaseError, match="skip/001-bad.md") as refused:
        load_turn_cases(tmp_path)
    assert reason in str(refused.value)


def test_a_written_case_reads_back_and_never_overwrites(tmp_path):
    first = write_turn_case(
        tmp_path, "backend.trace_problem", "token hết hạn rồi, gọi api báo unauthorized"
    )
    second = write_turn_case(tmp_path, "backend.trace_problem", "api lỗi")

    assert first.name == "001-token-het-han-roi-goi-api.md"
    assert second.name.startswith("002-")
    assert [c.inputs for c in load_turn_cases(tmp_path)] == [
        (("token hết hạn rồi, gọi api báo unauthorized", False),),
        (("api lỗi", False),),
    ]
    assert "hết hạn" in first.read_text(encoding="utf-8"), (
        "written readable, not escaped"
    )


def test_a_file_outside_the_set_is_refused_even_when_it_exists(tmp_path):
    """`file:` is a path someone typed; it may name only a file under the set,
    so a case cannot pull an arbitrary file into the model's input."""
    (tmp_path / "secret.md").write_text("not a message\n")
    root = tmp_path / "set"
    _write(
        root,
        "skip/001-bad.md",
        "---\nexpected_task: skip\nturn:\n  - file: ../secret.md\n---\n",
    )

    with pytest.raises(CaseError, match="is not a file under set/"):
        load_turn_cases(root)


def test_a_byte_order_mark_is_not_mistaken_for_missing_frontmatter(tmp_path):
    _write(
        tmp_path,
        "skip/001-bom.md",
        "\ufeff---\nexpected_task: skip\n---\nlol nice one\n",
    )

    (case,) = load_turn_cases(tmp_path)

    assert case.inputs == (("lol nice one", False),)
