"""Turning what someone typed into something worth reading.

The rule the whole module exists for: **split before cleaning.** Stripping
emoji or collapsing whitespace inside a `curl` corrupts the one thing in the
message that has to survive verbatim — and it is the thing the extractor is
looking for.
"""

from __future__ import annotations

from friday.text.transform import Attachment, render_attachments, transform


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
    """"the `correlationId` field" must not become "the field"."""
    cleaned = transform("cái field `correlationId` ấy, nó null 🤔")

    assert "correlationId" in cleaned.text
    assert cleaned.code == ("correlationId",)


def test_a_stack_trace_is_not_reflowed():
    raw = "```\nTraceback:\n  File \"pay.py\", line 20\n    → TypeError\n```"

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
    cleaned = transform("​API lỗi nè 😭😭  anh xem giúp em với  ")

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

    from friday.config import load_config
    from friday.triage.prefilter import Sensitive

    for key in ("TRIAGE_API_KEY", "RESPONDER_API_KEY"):
        os.environ.setdefault(key, "test-key")
    words = load_config(Path(__file__).resolve().parents[1] / "config.yaml")

    assert len(Sensitive(words.sensitive_words)) > 0


def test_a_string_where_a_list_belongs_is_refused():
    """A string iterates character by character, and the resulting rule holds
    every message containing the letter "l" — a failure that looks like the
    whole system going quiet."""
    import pytest

    from friday.config import ConfigError, _sensitive_words

    with pytest.raises(ConfigError, match="list of words"):
        _sensitive_words("lương")
