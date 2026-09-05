# 06: Two prompts escape the reporter's words twice

**What to build:** The triage model and the summariser see what a reporter
actually typed, instead of a version of it mangled by being escaped a second
time.

**Blocked by:** None (can start immediately — the mechanism it needs landed
with the responder's trust boundary)

**Status:** done

Both build a conversation section and then hand the whole rendered thing to
the wrapper:

```python
user_input(conversation(list(events)).render())
```

`conversation` escapes every line, and `user_input` escapes again, so a
reporter who writes `api <b>lỗi</b> & chậm` reaches the model as:

```
--- BEGIN USER INPUT ---
&lt;conversation&gt;
dana: api &amp;lt;b&amp;gt;lỗi&amp;lt;/b&amp;gt; &amp;amp; chậm
&lt;/conversation&gt;
--- END USER INPUT ---
```

Two things are wrong there, and only one of them is the escaping: the
`<conversation>` tag has been escaped into text as well, so the one label the
model was given is not a section any more. Every other agent here reads
labelled sections; these two read a description of one.

**The fix is the mechanism the responder now uses** — `quoted=True` on the
section, which puts the markers round a body that is already escaped and
leaves the tag alone. It was added so the responder would not join these two;
this is the other half of that.

**Extraction is not affected and must not be touched.** It wraps raw text —
`user_input(text)` — which is exactly right, and it is the one agent where
this would have hurt most: it copies `correlation_id` and `curl` verbatim
because both are matched by machine or pasted into a terminal, and an id that
went through two escapes is a value that no longer refers to anything.

## Why the two callers are not equally urgent

- **Triage** classifies and stops. A mangled `<b>` costs it very little; the
  words carrying the meaning are untouched, because escaping only moves
  `< > &`. This is the cheap half.
- **The summariser** is the expensive half. Its output is stored as the
  channel's derived summary, and **every later prompt for that room reads
  it** — so a mangled transcript does not end with that call, it becomes the
  room's memory of what was said. `channel_derived`'s own comment says this is
  why the value is escaped when it goes back out; nothing said the input was
  already double-escaped going in.

## The risk to weigh before changing triage

Triage runs on **every single mention** — the highest-volume prompt in the
system, and the one `config.yaml` warns about by name: the model there was
chosen by probe, and the note says not to change it without re-running one.
This ticket changes what that prompt looks like, not just its bytes: the
markers move inside a real section.

A green suite is not evidence that classification held. Take a handful of real
messages, run them through the classifier before and after, and compare the
labels and confidences.

- [x] A reporter's `<b>` reaches the triage model as `&lt;b&gt;`, once escaped
- [x] The same for the summariser
- [x] `<conversation>` is a real section in both, not an escaped string
- [x] Extraction is unchanged, and a test says so — it was never wrong, and
      it is the agent that can least afford to be
- [x] `test_only_a_prompt_whose_input_uses_the_markers_claims_them` still
      passes: both still claim the convention and both still wrap, by the
      other route
- [x] **A test that would have caught this**, and would catch the next one: no
      assembled prompt in any family contains a doubly-escaped entity. The
      bug lived because every existing test asked "is the hostile tag gone",
      and `&amp;lt;critical_reminder&amp;gt;` answers yes
- [x] Classification spot-checked against the real provider on real messages,
      before and after, with the result written into this ticket

## What the probe said

**First attempt, six invented messages: no label moved.** That answer was
worthless, and the ticket asked for real messages for exactly this reason —
invented text does not sit near a decision boundary, so it cannot show a
boundary being crossed.

**Second attempt, eight real captured mentions**, ordered so the ones
containing `<` or `&` come first, since those are the text this change
touches. Each classified through the new prompt and the old one in the same
process. Three labels moved.

**Third and fourth attempts added a control**: the *same* new prompt run
twice, alongside the old one. That is what settled it.

| run | new vs old (this change) | new vs new (the provider) |
| --- | --- | --- |
| 2 | 3 of 8 | not measured |
| 3 | 1 of 8 | 0 of 8 |
| 4 | 1 of 8 | 1 of 8 |

**The conclusion is that the probe cannot answer the question at this size.**
The one message that moved in more than one run is the same message the model
gave two different labels to on two identical calls — a mention carrying an
IP, a docs URL and `key: minichatbot`, sitting exactly between `skip` and
`access_request`. Its run-to-run flip is the same magnitude as the difference
being looked for, so nothing here separates the two. Triage is configured at
temperature 0; that buys less determinism from this provider than the number
suggests.

Reading it any other way would have been the mistake. Run 2 alone said "three
labels moved" and would have justified reverting a correct fix; run 3 alone
said "one moved, zero noise" and would have justified calling that one real.

**What the probe did establish**, which is not nothing:

- No message classified *differently in a way that reproduced* independently
  of the model's own variance.
- The change reaches the model: the messages carrying `<` and `&` are the ones
  whose confidence moved most.
- Every prompt other than triage's and the summariser's is byte-identical
  before and after — checked by capturing all seven assembled prompts and
  diffing, which is deterministic and free, and is the part of "did anything
  change" that does not need the provider at all.

**What would answer it properly**, and is not worth doing for this change: the
same messages replayed enough times per arm to put an interval round each
label's rate. That is a different exercise from a spot-check, and the thing it
would measure — whether a borderline mention lands on `skip` or on a human's
desk — is governed by `confidence_threshold`, which `config.yaml` already
flags as a placeholder nobody has tuned.

## Out of scope

- The order of triage's sections, or anything else about what it is told.
  This is one expression in each of two modules.
- `user_input` itself. Escaping raw text is its job and extraction depends on
  it; the two callers were passing it something already escaped.
