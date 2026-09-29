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

import pytest

from friday.kernel.responder.check import rejected

TEMPLATE = "Could you tell me the correlationId, or the curl you used?"


def test_a_draft_that_promises_to_do_something_is_refused():
    """The incident this exists for, recorded in `Responder.draft`'s own
    docstring: asked to request a correlationId, the model found one belonging
    to a different report and wrote a sentence promising work nobody would do.

    Short, no links, and it does contain `correlationId` — so every other rule
    here would have let it through."""
    said = rejected("ok có correlationId rồi, để anh trace thử", asking=TEMPLATE)

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

    # Ticket 12: the draft names no work. What is under test is the strip,
    # and a draft that happened to say "check" made this a test of `_WORK`.
    assert rejected("em đang chạy trên môi trường nào thế?", asking=asking) is None


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
        "ở https://internal.example/dash nhé, và cho anh xin correlationId, curl",
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

    assert rejected("em đang chạy trên môi trường nào thế?", asking=asking) is None


def test_a_link_in_capitals_is_still_a_link():
    """Discord linkifies `HTTPS://` exactly as it does `https://`, so a
    case-sensitive test was a one-character evasion that produced a real,
    clickable link in a message sent under the operator's name."""
    said = rejected(
        "cho anh xin correlationId nhe, o HTTPS://evil.example/x",
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

    Eight of the eleven questions this system asks name nothing that has to
    survive translation — "what access you need", "which document you mean".
    For those there is no way to tell a faithful Vietnamese rewording from a
    different question, so this rule says nothing and the other three carry
    it.

    The split is asserted below rather than only stated here.
    """
    assert (
        rejected(
            "hoàn toàn không liên quan gì cả",
            asking="Could you tell me what access you need?",
        )
        is None
    )
    # Ticket 12 dropped "xem" from this draft: `_WORK` names it now, so the
    # draft was about to demonstrate the point through the wrong rule.
    assert (
        rejected(
            "hoàn toàn không liên quan, https://x/y",
            asking="Could you tell me what access you need?",
        )
        is not None
    )


def test_which_questions_this_rule_binds_is_derived_not_counted():
    """The split the docstring above states, applied by `_KEPT` itself.

    It read "four of the seven" until ticket 13, and that was **wrong when it
    was written**: `project` was asked too, through a fallback that turned its
    field name into a sentence, so this system asked eight questions while the
    documentation said seven and nothing noticed. Deriving membership from the
    same pattern the rule matches on is what stops the prose drifting from the
    code a second time.

    Ticket 01 reshaped `TraceProblemParams` and moved both numbers: eleven
    subjects now, and `correlation_id` left the bound set because nobody is
    asked for one any more — it is read out of the response the reporter
    pasted, and "the response you got back" names no untranslatable word.
    `identifier` joined it, through `deviceId` and the same camelCase branch
    of `_KEPT` that used to catch `correlationId`.

    The eleven questions themselves are pinned in
    `tests/test_validation.py::test_the_questions_this_system_can_ask_are_written_down`;
    this asserts only which of them this rule binds.
    """
    from friday.kernel.responder.check import _KEPT
    from tests.test_validation import _asks

    asks = _asks()
    binds = sorted(
        subject for (_, subject), phrase in asks.items() if _KEPT.search(phrase)
    )

    assert binds == ["_traceable", "curl", "identifier"], (
        "which questions must survive translation has changed — the docstring "
        "above states this split"
    )
    assert len(asks) == 11, (
        "the number of questions changed; the docstring above states it"
    )


#: The three `ask_for_details` rows in `data/friday.db` — every message this
#: system has ever sent on the path with no person in it. Quoted literally,
#: because a paraphrase of a failure is a test of the paraphrase.
SENT_UNDER_THE_OPERATORS_NAME = (
    "correlationId nằm trong response header `x-request-id` đó em, em check "
    "trong Postman tab Headers hoặc DevTools Network là thấy. Không có thì em "
    "gửi anh cái curl em đang gọi cũng được, anh trace giúp.",
    "em gửi anh cái correlationId hoặc curl em gọi đi, a trace giúp",
    "em gửi anh cái correlationId hoặc curl em đang gọi với nhé, anh trace "
    "giúp. correlationId nằm trong response header `x-request-id` đó em.",
)


@pytest.mark.parametrize("draft", SENT_UNDER_THE_OPERATORS_NAME)
def test_the_asks_that_actually_went_out_are_refused(draft):
    """Ticket 12. The floor was in place — `05b4c51`, 2026-09-06 — and these
    went out on 2026-09-14 anyway.

    Each ends in `anh trace giúp`: the same promise as the incident
    `_PROMISES` was drawn from, in a spelling the list has no entry for. Each
    is short, carries no link, and names `correlationId` and `curl`, so every
    other rule here passes it.
    """
    said = rejected(draft, asking=TEMPLATE)

    assert said is not None, "this went out under the operator's name"
    assert "trace" in said or "check" in said


def test_a_draft_that_teaches_where_to_look_is_adding_content():
    """Not a promise, and still not wording.

    `config.yaml` justifies skipping approval with one sentence — what is
    being asked never changes, only the wording does. A draft that explains
    which Postman tab to open has changed what is being asked, from a model
    reading channel text nobody approved.
    """
    said = rejected(
        "correlationId nằm trong response header `x-request-id` đó em, em "
        "check trong Postman tab Headers là thấy nhé",
        asking=TEMPLATE,
    )

    assert said is not None and "check" in said


def test_an_ordinary_ask_names_no_work_and_still_passes():
    """The rule must not make the responder pointless. A question that asks
    for a thing — which is every question this system can ask — names no
    action and is untouched by it."""
    assert (
        rejected(
            "em ơi cho anh xin cái correlationId, hoặc cái curl em đang gọi nhé",
            asking=TEMPLATE,
        )
        is None
    )


def test_work_is_matched_as_a_word_not_as_a_substring():
    """`checklist` is not `check`, and a rule that cannot tell them apart
    refuses drafts for containing a longer word that happens to start the
    same way."""
    assert (
        rejected("anh gửi em cái checklist correlationId với curl nhé", asking=TEMPLATE)
        is None
    )


def test_work_spelled_with_decomposed_accents_is_still_work():
    """The same evasion `_PROMISES` already had closed, on the new rule:
    Vietnamese has two Unicode spellings of every accented letter and they
    compare unequal."""
    import unicodedata

    said = rejected(
        unicodedata.normalize("NFD", "anh kiểm tra giúp em, gửi correlationId nhé"),
        asking=TEMPLATE,
    )

    assert said is not None and "kiểm tra" in said


def test_a_model_cannot_switch_this_rule_off_from_inside_the_reason():
    """Found by review, and it is the shape this file exists to close, one
    level up.

    `_question_from_clarify` appends `clarify.because` — free text an
    extractor model wrote from channel content — inside the bracket. Reading
    the work list against the whole `asking` meant the untrusted half could
    exempt its own draft: write `trace` into `because`, and `anh trace giúp`
    goes out unread. The strip is the same `_REASON` the kept-words rule
    already applies, for a sharper reason than that one has.
    """
    asking = TEMPLATE + " (the logs need a trace on the gateway before this can move)"

    said = rejected(SENT_UNDER_THE_OPERATORS_NAME[1], asking=asking)

    assert said is not None, "a model wrote its own exemption"
    assert "trace" in said


def test_the_question_is_read_for_words_too_not_for_letters():
    """The exemption is symmetric with the rule it exempts from: a template
    that happens to contain `checklist` has not named `check`, the same way a
    draft containing `checklist` has not."""
    asking = "Could you tell me which item on the checklist failed?"

    said = rejected("anh check giúp em nhé", asking=asking)

    assert said is not None and "check" in said


def test_work_written_with_the_words_pushed_apart_is_still_work():
    """`re.escape` turns the space in `kiểm tra` into a literal one, so a
    model that broke the line between them — or typed two spaces — would walk
    straight past a rule that is supposed to be about words."""
    said = rejected("anh kiểm  tra giúp em, gửi correlationId nhé", asking=TEMPLATE)

    assert said is not None and "kiểm tra" in said


def test_the_same_verb_conjugated_is_the_same_verb():
    """Review's sharpest finding, and it walked straight through: `trace` was
    on the list and `tracing` was not, so a model that conjugated the one verb
    this system has already been burned by was unreadable to the rule written
    for it."""
    said = rejected(
        "em gửi anh cái correlationId hoặc curl nhé, anh tracing giúp",
        asking=TEMPLATE,
    )

    assert said is not None and "trace" in said


@pytest.mark.parametrize(
    "draft",
    [
        "em gửi anh cái correlationId hoặc curl em gọi đi, a lo nốt phần còn lại nhé",
        "em gửi anh cái correlationId hoặc curl nhé, anh xem cho",
    ],
)
def test_the_spellings_this_tickets_own_argument_rests_on_are_refused(draft):
    """Ticket 12's Why lists six spellings of one promise as the evidence that
    a phrasing list cannot close a language. The first version of `_WORK`
    caught four of them, having left `lo` and `xem` out as too ordinary — an
    argument that does not survive its own examples passing."""
    said = rejected(draft, asking=TEMPLATE)

    assert said is not None
    assert "lo" in said or "xem" in said
