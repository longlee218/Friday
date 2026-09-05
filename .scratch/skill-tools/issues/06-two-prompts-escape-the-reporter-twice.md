# 06: Two prompts escape the reporter's words twice

**What to build:** The triage model and the summariser see what a reporter
actually typed, instead of a version of it mangled by being escaped a second
time.

**Blocked by:** None (can start immediately — the mechanism it needs landed
with the responder's trust boundary)

**Status:** ready-for-agent

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

- [ ] A reporter's `<b>` reaches the triage model as `&lt;b&gt;`, once escaped
- [ ] The same for the summariser
- [ ] `<conversation>` is a real section in both, not an escaped string
- [ ] Extraction is unchanged, and a test says so — it was never wrong, and
      it is the agent that can least afford to be
- [ ] `test_only_a_prompt_whose_input_uses_the_markers_claims_them` still
      passes: both still claim the convention and both still wrap, by the
      other route
- [ ] **A test that would have caught this**, and would catch the next one: no
      assembled prompt in any family contains a doubly-escaped entity. The
      bug lived because every existing test asked "is the hostile tag gone",
      and `&amp;lt;critical_reminder&amp;gt;` answers yes
- [ ] Classification spot-checked against the real provider on real messages,
      before and after, with the result written into this ticket

## Out of scope

- The order of triage's sections, or anything else about what it is told.
  This is one expression in each of two modules.
- `user_input` itself. Escaping raw text is its job and extraction depends on
  it; the two callers were passing it something already escaped.
