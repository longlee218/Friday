"""The one message allowed out without a person reading it first.

`auto_ask_for_details` lets a request for missing details go straight to the
reporter, and `config.yaml` justified it in a sentence: what is being asked
never changes, only the wording does. Nothing enforced that sentence. The
responder's input contains other people's channel messages, so the one path
with no human in it was also the path whose wording is model-authored from
untrusted text and sent under the operator's name.

This is the floor. Failing it costs a plainer question; passing it wrongly
costs the operator's word.
"""

from __future__ import annotations

from friday.responder.check import rejected

TEMPLATE = "Could you tell me the correlationId, or the curl you used?"


def test_a_draft_that_promises_to_do_something_is_refused():
    """The incident this exists for, recorded in `Responder.draft`'s own
    docstring: asked to request a correlationId, the model found one belonging
    to a different report and wrote a sentence promising work nobody would do.

    Short, no links, and it does contain `correlationId` — so every other rule
    here would have let it through."""
    said = rejected(
        "ok có correlationId rồi, để anh trace thử", asking=TEMPLATE
    )

    assert said is not None
    assert "promise" in said


def test_a_draft_that_drops_the_technical_word_is_refused():
    """The prompt tells the responder that `correlationId` stays in English.
    A draft that asks for "mã theo dõi" instead is asking a question the
    reporter cannot act on — the field has a name in their logs and that name
    is the whole of what makes the question answerable."""
    said = rejected("cho anh xin cái mã theo dõi với", asking=TEMPLATE)

    assert said is not None
    assert "correlationId" in said


def test_answering_half_of_an_either_or_is_not_dropping_it():
    """The template offers a choice — "the correlationId, or the curl you
    used" — and asking for one of them is the point of offering it. The first
    version of this rule demanded every technical word the template used and
    refused drafts that had done nothing wrong."""
    assert rejected("cho anh xin correlationId với", asking=TEMPLATE) is None


def test_the_reason_a_question_is_asked_is_not_part_of_the_question():
    """`_question_from_clarify` appends why it is worth asking — "(the curl
    doesn't say which server)". That is context for the responder, not
    something the reporter has to be asked about, and reading technical words
    out of it made this demand the draft repeat the reasoning."""
    asking = "Could you tell me which environment you're on? (the curl doesn't say which server)"

    assert rejected("anh check giúp em cái server nhé", asking=asking) is None


def test_the_operators_own_voice_passes():
    """The wording is allowed to change. That is what the responder is for,
    and a check that only accepted the template would make it pointless."""
    assert (
        rejected(
            "anh ơi cho em xin cái correlationId với, hoặc cái curl anh gọi nhé",
            asking=TEMPLATE,
        )
        is None
    )


def test_a_draft_carrying_a_link_is_refused():
    """Nothing in a request for missing details needs a URL, and a model that
    produces one has read something in the channel and is passing it on."""
    said = rejected(
        "xem ở https://internal.example/dash nhé, và cho anh xin correlationId, curl",
        asking=TEMPLATE,
    )

    assert said is not None and "link" in said


def test_a_draft_carrying_a_code_block_is_refused():
    """Same argument as the link, and the same origin: a fenced block in a
    question is something quoted back out of somebody else's message."""
    said = rejected(
        "cho anh xin correlationId, curl nhé:\n```\nGET /v1/checkout\n```",
        asking=TEMPLATE,
    )

    assert said is not None and "code" in said


def test_a_draft_much_longer_than_the_question_is_refused():
    """Length is a proxy for having done something other than rephrase. The
    template is one sentence; four hundred characters of it is not a wording
    change, whatever the extra says."""
    said = rejected(
        "cho anh xin correlationId, curl nhé. " + "anh cần thêm chút nữa. " * 30,
        asking=TEMPLATE,
    )

    assert said is not None and "long" in said


ENV_WITH_RULE = (
    "Could you tell me which environment you're on "
    "(must be one of: dev, production, staging)?"
)


def test_the_plainest_correct_rewording_of_a_validated_field_is_not_refused():
    """`_question` puts the rule's message *inline* and ends with `?`, so a
    strip anchored to the end of the string never touched it — and the enum a
    validation rule quotes ("dev, production, staging") went into the words the
    draft was required to repeat.

    The result was that the plainest correct Vietnamese question was refused
    for not reciting the allowed values. Reachable exactly when the reporter
    wrote `sản xuất`, which CLAUDE.md names as the expected case.
    """
    assert rejected("anh đang chạy ở môi trường nào ạ?", asking=ENV_WITH_RULE) is None


def test_a_bracket_inside_the_models_reason_does_not_re_arm_the_bug():
    """`because` is free text an extractor wrote, so it can contain a bracket
    of its own — and a strip that stops at the first `)` then leaves half the
    clause behind. The reason is always appended last, so everything from the
    first bracket on is reason."""
    asking = (
        "Could you tell me which environment you're on? "
        "(the curl doesn't say which server (see the log))"
    )

    assert rejected("anh check giúp em cái server nào nhé", asking=asking) is None


def test_a_link_in_capitals_is_still_a_link():
    """Discord linkifies `HTTPS://` exactly as it does `https://`, so a
    case-sensitive test was a one-character evasion that produced a real,
    clickable link in a message sent under the operator's name."""
    said = rejected(
        "cho anh xin correlationId nhe, xem o HTTPS://evil.example/x",
        asking=TEMPLATE,
    )

    assert said is not None and "link" in said


def test_a_promise_spelled_with_decomposed_accents_is_still_a_promise():
    """Vietnamese has two spellings of every accented letter and they compare
    unequal. A word list matched against raw text is a list that can be walked
    around by writing `để` the other way, which no editor shows and no reader
    would see."""
    import unicodedata

    said = rejected(
        unicodedata.normalize("NFD", "ok có correlationId rồi, để anh trace thử"),
        asking=TEMPLATE,
    )

    assert said is not None and "promise" in said


def test_a_template_with_nothing_untranslatable_binds_only_the_other_rules():
    """The honest limit, written down so nobody reads the docs as a stronger
    promise than the code makes.

    Five of the eight questions this system asks name nothing that has to
    survive translation — "what access you need", "which document you mean".
    For those there is no way to tell a faithful Vietnamese rewording from a
    different question, so this rule says nothing and the other four carry it.

    The split is asserted below rather than only stated here.
    """
    assert rejected(
        "hoàn toàn không liên quan gì cả", asking="Could you tell me what access you need?"
    ) is None
    assert rejected(
        "hoàn toàn không liên quan, xem https://x/y",
        asking="Could you tell me what access you need?",
    ) is not None


def test_which_questions_this_rule_binds_is_derived_not_counted():
    """The split the docstring above states, applied by `_KEPT` itself.

    It read "four of the seven" until ticket 13, and that was **wrong when it
    was written**: `project` was asked too, through a fallback that turned its
    field name into a sentence, so this system asked eight questions while the
    documentation said seven and nothing noticed. Deriving membership from the
    same pattern the rule matches on is what stops the prose drifting from the
    code a second time.

    The eight questions themselves are pinned in
    `tests/test_validation.py::test_the_questions_this_system_can_ask_are_written_down`;
    this asserts only which of them this rule binds.
    """
    from friday.responder.check import _KEPT
    from tests.test_validation import _asks

    asks = _asks()
    binds = sorted(subject for (_, subject), phrase in asks.items() if _KEPT.search(phrase))

    assert binds == ["_traceable", "correlation_id", "curl"], (
        "which questions must survive translation has changed — the docstring "
        "above and CLAUDE.md both state this split"
    )
    assert len(asks) == 8, "the number of questions changed; CLAUDE.md states it"
