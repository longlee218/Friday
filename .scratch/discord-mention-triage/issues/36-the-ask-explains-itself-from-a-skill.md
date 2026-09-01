# 36: The ask explains itself, from a skill

**What to build:** When the agent asks for a missing detail, it says how to
find it — in the operator's own written words. "Send me the correlationId"
becomes "send me the correlationId, it's the `x-request-id` header on the
response — or the curl, either works". A reporter who does not know what is
being asked of them gets told, without waiting for a person.

**Blocked by:** 35 (tell the operator what they were asked)

**Status:** done

## The line this ticket draws

The request for a missing detail is the one message that goes out under the
operator's name with **no approval step**. The reasoning has always been that
asking cannot be harmful however it is worded — the risk in this system is in
*answering*.

Explaining what we asked for stretches that, and it should stretch exactly this
far and no further:

> **Explaining our own request, in the operator's written words, is asking.
> Inventing an explanation is answering.**

What separates them is where the words come from. The agent does not know what
a correlationId is in this company's systems, and `PERSONA.md` is explicit that
it does not invent. So the explanation comes from `skills/` — the operator
writes it once — or it does not happen.

Three tiers, and only the first two are automatic:

| | When | Approval |
|---|---|---|
| **Prevent** | the first ask already carries the how | none — still asking |
| **Explain** | they ask anyway, and a skill covers it | none — the operator's own words |
| **Hand over** | no skill covers it | the operator, with the question (ticket 35) |

## Blocked by 35 because

The third tier *is* ticket 35. Without it, "no skill covers this" resolves to
silence: the reporter has asked something nobody will answer and the operator
is not told they asked.

## Note on the skills that exist

`trace-a-request` is written for the agent — "find the log lines for one
request, given a correlationId". The skill this ticket needs is written for the
**reporter**: where a correlationId is found, and what to send instead when
there is none. Writing it is part of this ticket; a mechanism with nothing to
draw on is not demoable.

## Acceptance criteria

- [x] The request for a missing detail draws on a skill when one covers that
      detail, and the skill's content reaches the message
- [x] With no skill covering it, the ask is exactly what it is today — the
      absence of knowledge is never filled in by the model
- [x] After the cap on re-asking, the task goes to the operator carrying the
      question, per ticket 35
- [x] A skill exists for finding a correlationId, written for the person being
      asked rather than for the agent
- [x] The extra prompt cost is paid once, not per call: the catalogue is
      already in the stable front of the prompt and this must not move it
- [x] A test proves that with the skill library empty the message is unchanged,
      and with it populated the guidance appears

## What it came to

The mechanism already existed — the responder had `fetch_skill` and the
catalogue — so the ticket was one skill, one paragraph and one guard. Verified
on the live provider, five samples each way:

    no skill:    chưa trace được, em gửi anh correlationId hoặc cái curl em gọi nhé
    with skill:  anh gửi mình correlationId nhé — nó nằm ở response header `x-request-id`

No invented location without a skill; the skill's words with one. Ticket 41's
stranger rule was found to hold about half the time while it lived only in the
persona, and every time once restated beside the ask — so it lives in both.
