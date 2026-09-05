# 07: A stored summary is escaped again on the way back out

**What to build:** Text this system wrote, stored, and later reads back into a
prompt is escaped once in total — not once on the way in and again on the way
out.

**Blocked by:** None (independent of 06, which fixed a different mechanism)

**Status:** needs-triage — **the decision is where to escape, and it is not
obvious.** Named below.

Found while reviewing ticket 06, not from a thread. It is the same word —
"escaped twice" — through a different mechanism, and 06's fix does not touch
it. 06's own guard test says so in its docstring rather than quietly not
covering it.

## The round trip

The summariser is shown the transcript with a reporter's `<b>` escaped to
`&lt;b&gt;` — correct, one escape, that is 06's fix. It writes a summary. If
that summary quotes the reporter, the model may well copy what it was shown,
so `&lt;b&gt;` is what gets stored as the channel's `derived`.

`channel_derived` then escapes on the way out, and says why it must:

> Machine-written derived content (the rebuilder's output). Not trusted: a bug
> in the summariser or a hallucinated note lands here, and the value flows
> into a system prompt. Escaped at the seam.

So the value reaches every later prompt for that room as
`&amp;lt;b&amp;gt;`. Reproduced directly:

```python
channel_derived(ChannelContext(derived={"summary": "api &lt;b&gt;loi&lt;/b&gt;"}))
# -> summary: api &amp;lt;b&amp;gt;loi&amp;lt;/b&amp;gt;
```

**Both escapes are individually right**, which is what makes this harder than
06. The one on the way out cannot simply go: derived content is
model-written, and the comment above is the reason it is escaped at all. The
one on the way in cannot simply go either — it is what stops a reporter
closing a section in the transcript the summariser reads.

This is worse than 06's triage half and about as bad as its summariser half,
for the same reason: it is not one mangled call. Every later prompt for that
room reads `derived`, so a mangled summary is the room's memory of what was
said.

## The open decision

- **A. Unescape on the way in to storage.** The summariser's output is
  normalised back to plain text before it is stored, so `derived` holds what a
  person would read and the one escape happens at the prompt seam like
  everything else. Puts an unescape in the codebase, which is a thing to be
  careful with, and it is lossy if a reporter genuinely typed `&lt;`.
- **B. Do not escape derived on the way out.** Rejected on its face, but worth
  writing down so nobody re-proposes it: `channel_derived`'s comment is the
  record of why it escapes, and a hallucinated note reaching a system prompt
  unescaped is the failure it was added for.
- **C. Do not escape the transcript the summariser reads.** Its input is one
  quoted block behind `trust_boundary` markers, so arguably the markers carry
  the boundary and the escape is belt-and-braces. But `user_input`'s own
  docstring says the opposite — the markers are text a reporter can type, and
  escaping is the half that holds — so this trades the guarantee for the
  cosmetics.
- **D. Accept it.** The damage is cosmetic where 06's was: the words carrying
  the meaning survive, only `< > &` are mangled, and a summary is prose rather
  than a value matched by machine. The reason not to accept it is that it
  compounds — each rebuild of the summary re-reads the last one.

**Whoever takes this should check D's compounding claim first**, because if it
is true the ticket is worth more than it looks, and if it is false D is the
cheap answer. It is a question about whether the rebuild reads the previous
summary, which the code answers.

- [ ] The compounding question is answered from the code before a choice is made
- [ ] The decision is recorded here with its reason
- [ ] A reporter's `<b>` survives the round trip — transcript, summary,
      storage, `channel_derived` — as `&lt;b&gt;` in the prompt that finally
      reads it, escaped once
- [ ] 06's guard is widened to cover an assembled prompt built from stored
      `derived`, which is the case it explicitly does not cover today
- [ ] Whatever is chosen leaves `channel_derived`'s guarantee intact: a
      hallucinated note still cannot open a section in a system prompt, and a
      test still says so

## Out of scope

- `channel_overrides` and `channel_base`. Overrides go through the same
  escaping and could have the same round trip, but nothing writes them
  programmatically — the operator types them — so there is no second escape
  to stack. Worth a glance while in there, not a reason to widen the ticket.
- Anything about what the summariser is told to write. This is about what
  happens to what it wrote.
