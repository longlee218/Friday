"""The shape an extractor answers, built from the task type it fills.

One validated object per extraction: that type's own parameters, plus which of
its fields the extractor wants to ask the reporter about and why. Board
`every-answer-has-a-shape`, D7.

**It was two things.** The fields arrived as text and were scraped; the request
for more detail arrived as a separate `ask_for_fields` tool call that wrote
into a per-run capture the caller read back afterwards. So one extraction
produced two answers by two mechanisms, and neither was the return value of
anything. Now the model calls one tool, generated from the shape below, and the
arguments are validated before anybody sees them.

**What the deleted tool's enum gave, `ask_about` still gives**: the field names
are a closed set of that type's own askable fields, so an extractor cannot ask
the reporter about a field that does not exist. That property was the whole
argument for the tool being a factory, and losing it in the move would have
been the move's one real cost.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, make_dataclass
from dataclasses import fields as dataclass_fields
from functools import cache
from typing import Any, Literal, get_type_hints

from friday.kernel.domain.tasks import Params, askable_fields

__all__ = ["Clarify", "answer_shape", "params_and_clarify"]


@dataclass(frozen=True, slots=True)
class Clarify:
    """The extractor's own request: these fields, because of this.

    Intent, not words — `fields` names which of the type's own fields it
    means, closed to a per-type enum so the model cannot invent one that does
    not exist. The Responder turns this into the sentence a reporter reads;
    this module never writes one.
    """

    fields: tuple[str, ...]
    because: str


#: What `ask_about` and `because` are for, in the words the model reads. Beside
#: the shape they are added to rather than in the prompt module, for the reason
#: every other field's `doc` lives on the field: one string, and both the
#: prompt's rendering of the shape and the tool's own parameter description are
#: generated from it.
_ASK_ABOUT = (
    "which of the fields above you want the reporter asked about, because "
    "something in what they wrote makes it worth asking even though nothing "
    "here requires it structurally — an ambiguity, a detail the report implies "
    "but does not state. Empty when there is nothing worth asking."
)
_BECAUSE = (
    "why, in one short phrase — what you read that makes those worth asking. "
    "Empty when you are asking about nothing."
)


@cache
def answer_shape(params_cls: type[Params]) -> type:
    """The dataclass one task type's extractor answers.

    **Cached, and the identity matters.** `build_extractor` checks that a
    harness answers the shape its extractor claims, and `isinstance` decides
    when a run has answered at all — both compare classes, so generating a
    fresh one per call would make every such check fail in a way no type
    checker would catch.

    **Flat, not nested.** A `{"params": {...}, "ask_about": [...]}` wrapper
    would be one less generated class and would tell the model less: the
    shape's description and the tool's parameters are both generated from the
    fields, and a field called `params` of type `TraceProblemParams` describes
    nothing. Flat also means `fits` refuses an object whose keys are all
    unknown, which a wrapper would quietly turn into an empty answer — the
    exact failure that rule exists for.

    Annotations are resolved from `params_cls` rather than copied off
    `field.type`: `from __future__ import annotations` leaves those as source
    text, and a generated class carries no module whose globals could resolve
    them back.
    """
    try:
        hints = get_type_hints(params_cls)
    except NameError:
        # A schema whose annotations cannot be resolved from its own module's
        # globals — one declared inside a function against a locally aliased
        # import. The same fallback `describe` makes, for the same reason, and
        # with one more consequence here: the generated class carries the
        # source text instead, which pydantic resolves against *this* module's
        # globals. That covers `str | None` and not `_Optional[str]`. Real
        # schemas are module-level; this is so a test that declares one inline
        # fails on its own terms rather than inside a shape builder.
        hints = {}
    askable = askable_fields(params_cls)
    # A *tuple* inside `Literal`, which is not the spelling anyone writes by
    # hand and is the only one available here: `Literal["a", "b"]` needs its
    # members as separate subscript arguments, and these are computed. Python
    # accepts a tuple as equivalent; mypy does not follow it, hence the
    # suppression below rather than a real typing gap.
    carried = [
        (f.name, hints.get(f.name, f.type), _same_default(f))
        for f in dataclass_fields(params_cls)
    ]
    return make_dataclass(
        f"{params_cls.__name__}Answer",
        [
            *carried,
            (
                "ask_about",
                list[Literal[askable]],  # type: ignore[valid-type]
                field(default_factory=list, metadata={"doc": _ASK_ABOUT}),
            ),
            ("because", str, field(default="", metadata={"doc": _BECAUSE})),
        ],
        frozen=True,
        # **So a traceback names somewhere a reader can go.** A generated
        # class defaults its `__module__` to whatever called `make_dataclass`,
        # and appears in a stack trace as a name with no file behind it —
        # `TraceProblemParamsAnswer`, which greps to nothing. Both of these exist
        # for the person reading the failure, not for the code.
        namespace={
            "__module__": __name__,
            "__doc__": (
                f"What the {params_cls.__name__} extractor answers: that "
                f"type's own fields, plus which of them it wants the reporter "
                f"asked about and why. Generated by `answer_shape` in "
                f"{__name__}; there is no source file declaring it."
            ),
        },
    )


def params_and_clarify(
    answer: Any, params_cls: type[Params]
) -> tuple[Params, Clarify | None]:
    """One validated answer, back into the two values the graph acts on.

    Named for what it returns. It was `split`, which says that something is
    divided and not into what — and at the call site, bare, it could have been
    splitting a string.

    The `Clarify` is `None` when nothing was asked about — an empty list is
    the model saying there is nothing worth asking, which is different from a
    request with no fields in it and must not become one. `because` on its own
    is not a request either: the fields are what a question can be built from.
    """
    filled = params_cls(
        **{f.name: getattr(answer, f.name) for f in dataclass_fields(params_cls)}
    )
    asked = tuple(answer.ask_about)
    return filled, Clarify(fields=asked, because=answer.because) if asked else None


def _same_default(source) -> Any:
    """A copy of one field's default, whichever kind it has.

    Every `Params` field has one — nothing fills these in at construction —
    and a generated field that lost it would make the shape's first required
    argument a positional the model has no way to know about.
    """
    if source.default_factory is not MISSING:
        return field(default_factory=source.default_factory, metadata=source.metadata)
    return field(default=source.default, metadata=source.metadata)
