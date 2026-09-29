"""Turning what someone typed into something worth reading.

The rule the whole module exists for: **split before cleaning.** Stripping
emoji or collapsing whitespace inside a `curl` corrupts the one thing in the
message that has to survive verbatim — and it is the thing the extractor is
looking for.
"""

from __future__ import annotations

from datetime import UTC

from friday.kernel.text_transform import (
    Attachment,
    redact,
    render_attachments,
    transform,
)

# --- code survives exactly ---------------------------------------------------


def test_a_curl_keeps_its_line_continuations():
    """A curl with its newlines eaten is not a curl. Whoever is handed it is
    meant to paste it into a terminal."""
    raw = (
        "lỗi rồi anh ơi 😭\n"
        "```bash\n"
        "curl -X POST https://api/pay \\\n"
        "  -H 'x-id: abc'\n"
        "```\n"
        "cái này trên   prod nhé"
    )

    cleaned = transform(raw)

    assert cleaned.code == ("curl -X POST https://api/pay \\\n  -H 'x-id: abc'",)
    # And it is still in the text, because the text is what gets stored.
    assert "-H 'x-id: abc'" in cleaned.text


def test_the_prose_around_code_is_still_cleaned():
    cleaned = transform("lỗi rồi 😭\n```\ncurl -X GET /a\n```\ntrên   prod   nhé")

    assert "😭" not in cleaned.text
    assert "trên prod nhé" in cleaned.text


def test_an_inline_span_stays_a_word_in_the_sentence():
    """ "the `correlationId` field" must not become "the field"."""
    cleaned = transform("cái field `correlationId` ấy, nó null 🤔")

    assert "correlationId" in cleaned.text
    assert cleaned.code == ("correlationId",)


def test_a_stack_trace_is_not_reflowed():
    raw = '```\nTraceback:\n  File "pay.py", line 20\n    → TypeError\n```'

    assert transform(raw).code == (
        'Traceback:\n  File "pay.py", line 20\n    → TypeError',
    )


# --- what cleaning removes ---------------------------------------------------


def test_vietnamese_survives():
    """The reason the emoji ranges are enumerated rather than swept. A broad
    "anything unusual" rule eats combining diacritics."""
    said = "Cho em xin cái correlationId nhé, API đang lỗi ở môi trường staging"

    assert transform(said).text == said


def test_emoji_and_invisible_characters_go():
    cleaned = transform("\u200bAPI lỗi nè 😭😭  anh xem giúp em với  ")

    assert cleaned.text == "API lỗi nè anh xem giúp em với"


def test_a_custom_discord_emoji_is_markup_not_a_word():
    assert transform("deploy xong <:party:12345> 🎉").text == "deploy xong"


def test_a_paragraph_break_is_structure_and_survives():
    """A message flattened to one line reads as one thought when it was three."""
    assert transform("xin chào\n\n\n\nđây là dòng ba").text == (
        "xin chào\n\nđây là dòng ba"
    )


def test_nothing_at_all_is_not_an_error():
    assert transform(None).text == ""
    assert transform("").text == ""
    assert not transform("   \n\n  ")


# --- attachments -------------------------------------------------------------


def test_an_attachment_is_named_so_it_survives_being_stored():
    """They were dropped at the provider and never mentioned again: someone
    posts the screenshot of the error and the system asks them what the error
    was. A message is one string everywhere below the provider, so an
    attachment that is not in that string does not exist."""
    line = render_attachments(
        (Attachment("error.png", "image/png"), Attachment("log.txt"))
    )

    assert line == "[attached: error.png (image/png), log.txt]"


def test_no_url_is_put_in_front_of_a_model():
    """A URL in a prompt is an invitation to fetch something, and nothing here
    is allowed to."""
    line = render_attachments((Attachment("error.png", "image/png"),))

    assert "http" not in line


def test_no_attachments_says_nothing():
    assert render_attachments(()) == ""


# --- the list the operator maintains -----------------------------------------


def test_the_configured_list_is_reachable_and_not_empty():
    """`sensitive_words` is read at startup and nothing else fills it. An
    empty list is a valid choice and a silent one — this is the check that the
    shipped configuration actually carries the list it documents."""
    import os
    from pathlib import Path

    from friday.kernel.config import load_config
    from friday.kernel.triage.prefilter import Sensitive

    for key in ("OPENROUTER_API_KEY",):
        os.environ.setdefault(key, "test-key")
    words = load_config(Path(__file__).resolve().parents[1] / "config.yaml")

    assert len(Sensitive(words.sensitive_words)) > 0


def test_a_string_where_a_list_belongs_is_refused():
    """A string iterates character by character, and the resulting rule holds
    every message containing the letter "l" — a failure that looks like the
    whole system going quiet."""
    import pytest

    from friday.kernel.config import ConfigError, _sensitive_words

    with pytest.raises(ConfigError, match="list of words"):
        _sensitive_words("lương")


# --- the wiring, not just the function ---------------------------------------


def _discord_message(text: str, *, files=()):
    """The shape `normalise` reads, with nothing it does not read."""
    from datetime import datetime
    from types import SimpleNamespace

    return SimpleNamespace(
        id=1,
        content="",
        clean_content=text,
        author=SimpleNamespace(id=9, display_name="dana"),
        created_at=datetime.now(UTC),
        mentions=[],
        role_mentions=[],
        channel=SimpleNamespace(id=5),
        guild=None,
        reference=None,
        attachments=[
            SimpleNamespace(filename=name, content_type=kind) for name, kind in files
        ],
    )


def test_the_provider_actually_calls_the_transform():
    """Both halves were removed from `normalise` and the whole suite still
    passed. Every test here drove `transform()` directly, so the layer was
    covered and its one call site was not — which is the only part that
    decides whether any of it runs.
    """
    from friday.kernel.providers.discord.normalise import normalise

    event = normalise(
        _discord_message("API lỗi nè 😭😭   anh xem giúp em với  "),
        me_id=9,
        my_role_ids=frozenset(),
    )

    assert event.text == "API lỗi nè anh xem giúp em với"


def test_the_provider_carries_the_code_it_found():
    from friday.kernel.providers.discord.normalise import normalise

    event = normalise(
        _discord_message("lỗi rồi\n```\ncurl -X GET /pay\n```"),
        me_id=9,
        my_role_ids=frozenset(),
    )

    assert event.code == ("curl -X GET /pay",)
    assert "curl -X GET /pay" in event.text


def test_the_provider_names_the_attachments():
    """They were dropped here, at this exact call, and nowhere else."""
    from friday.kernel.providers.discord.normalise import normalise

    event = normalise(
        _discord_message("cái này nè", files=[("error.png", "image/png")]),
        me_id=9,
        my_role_ids=frozenset(),
    )

    assert event.attachments[0].filename == "error.png"
    assert "[attached: error.png (image/png)]" in event.text


# --- redact (ticket 07: verbatim material becomes an artifact) --------------


def test_redact_swaps_the_code_for_the_given_ref():
    raw = "lỗi rồi anh ơi\n```bash\ncurl -X GET /pay\n```\ntrên prod nhé"

    said = redact(raw, ["[artifact a1: a curl]"])

    assert "curl -X GET /pay" not in said
    assert "[artifact a1: a curl]" in said
    assert "trên prod nhé" in said


def test_redact_takes_one_ref_per_span_in_order():
    """Fenced blocks are matched before inline spans (`_FENCED` then
    `_INLINE` — the module's own comment on why), so `code`'s order is
    fenced-then-inline, not left-to-right through the raw text. Two spans of
    the same kind avoid that reordering and pin the property `redact` and
    `transform` actually share: same kind, same document order."""
    raw = "```curl -X GET /pay``` rồi ```curl -X POST /refund```"

    said = redact(raw, ["[artifact a1: get]", "[artifact a2: post]"])

    assert "[artifact a1: get]" in said
    assert "[artifact a2: post]" in said
    assert said.index("[artifact a1") < said.index("[artifact a2")


def test_redact_refuses_the_wrong_number_of_refs():
    import pytest

    raw = "```curl -X GET /pay```"

    with pytest.raises(ValueError):
        redact(raw, [])
    with pytest.raises(ValueError):
        redact(raw, ["one", "too many"])


def test_redact_of_nothing_is_nothing():
    assert redact(None, []) == ""
    assert redact("", []) == ""


def test_redact_of_prose_with_no_code_needs_no_refs():
    assert redact("không có code gì cả", []) == "không có code gì cả"


def test_transform_and_redact_split_the_same_raw_text_the_same_way():
    """`redact` must find exactly the spans `transform` would — otherwise a
    caller building refs from `transform(raw).code` hands `redact` the wrong
    count for the same `raw`."""
    raw = "`id` và ```curl -X GET /pay```"

    cleaned = transform(raw)

    assert redact(raw, ["ref"] * len(cleaned.code))  # does not raise
