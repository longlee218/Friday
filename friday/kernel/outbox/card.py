"""The approval card, told in full.

A reply goes out in the operator's name, into a channel other people read. The
card the operator approves is the last place a lie in the draft can be caught,
so it tells the whole truth about what pressing *Approve* would send (DESIGN-v2
§12): the exact bytes, where they go and to whom, what any link or mention
actually resolves to underneath its display text, and whether a secret was found
in the draft and redacted.

Built here, in the kernel, rather than in the Discord adapter: the truth about a
draft is policy, not presentation, and it must read the same however it is
delivered. The adapter transports `render()`'s text and adds only the buttons —
an application-only Discord feature — around it.

**Redaction runs on the draft.** The reply that will actually be sent is
`scrub`bed before it is queued (`pool._propose`), so a secret never leaves even
if the card is approved. The card shows those same scrubbed bytes — what will go
out, exactly — and flags, from the *original* draft, that a redaction happened,
so the operator is not approving a `[REDACTED]` without knowing why it is there.
"""

from __future__ import annotations

import re

from friday.kernel.domain.conversation import ConversationId
from friday.kernel.ops.redact import (
    matches_secret_pattern,
    matches_secret_value,
    scrub,
)

__all__ = ["render"]

#: A markdown link hides its destination behind display text: `[our docs](evil)`
#: reads as trustworthy and points anywhere. The card reveals the destination.
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")

#: A bare URL still names a host the operator should see named.
_BARE_URL = re.compile(r"(?<!\()\bhttps?://[^\s>)]+")

#: Discord's silent pings: a user/role/channel mention shows as a name in the
#: client but is an id in the bytes, and `@everyone`/`@here` notify a whole
#: server. Either is worth surfacing before a message goes out under your name.
_USER = re.compile(r"<@!?(\d+)>")
_ROLE = re.compile(r"<@&(\d+)>")
_CHANNEL = re.compile(r"<#(\d+)>")
_BROADCAST = re.compile(r"@(everyone|here)\b")


def render(text: str, *, destination: ConversationId, reply_id: int) -> str:
    """The operator-facing approval card for one drafted reply.

    `text` is the *original* draft — flags and expansions are read from it — and
    the shown bytes are `scrub`bed, matching exactly what `pool._propose` queues
    as the reply. `destination` is where the reply posts.

    **The audience is public.** A card is only ever raised for a `REPLY`, which
    posts into the conversation as the watched account — so it leaves the
    operator-only channel and reaches whoever is in that conversation, which is
    the public/private distinction §12 is about. The operator-only kinds (an
    alert, a finding, this card itself) are never a `REPLY` and never reach here.
    A finer audience axis — a per-user private row — is deferred until there are
    personal rows to need it (§9.1, §16); `ConversationId` carries no marker for
    one today, so there is nothing finer to compute here honestly.

    A reply has **no attachments to expand**: the `Outbound` row is text plus a
    destination (§3.3), with no attachment channel — the ticket's
    "links/mentions/attachments" is links and mentions here, and there is
    nothing else in the bytes to reveal.
    """
    shown = scrub(text)
    lines = [
        f"**Reply to {destination} — public, goes out as you**",
        f"> {shown}",
    ]
    notes = _links(text) + _mentions(text) + _secrets(text)
    if notes:
        lines.append("")
        lines.extend(notes)
    lines.append(f"_reply {reply_id}_")
    return "\n".join(lines)


def _links(text: str) -> list[str]:
    notes = []
    for label, url in _MD_LINK.findall(text):
        notes.append(f"🔗 link “{label}” actually points to {url}")
    for url in _BARE_URL.findall(text):
        notes.append(f"🔗 links out to {url}")
    return notes


def _mentions(text: str) -> list[str]:
    notes = []
    for user_id in _USER.findall(text):
        notes.append(f"@ pings user {user_id}")
    for role_id in _ROLE.findall(text):
        notes.append(f"@ pings role {role_id}")
    for channel_id in _CHANNEL.findall(text):
        notes.append(f"# refers to channel {channel_id}")
    for which in _BROADCAST.findall(text):
        notes.append(f"@ notifies @{which} — the whole channel")
    return notes


def _secrets(text: str) -> list[str]:
    """Both flags §12 asks for, read from the original draft before it was
    scrubbed. Separate lines so the operator sees *why* a `[REDACTED]` is in the
    bytes above — a known-secret leak, or something merely shaped like one."""
    notes = []
    if matches_secret_value(text):
        notes.append("⚠ a declared secret was found in the draft and redacted")
    if matches_secret_pattern(text):
        notes.append("⚠ something shaped like a credential was found and redacted")
    return notes
