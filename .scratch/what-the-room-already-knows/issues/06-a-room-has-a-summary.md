# 06: A room has a summary, and the extractor reads it

**What to build:** A room accumulates a structured summary of itself, and the
extractor reads it. Today every room's derived context is `{}`, the summariser
is commented out of configuration, and even enabled it would only fire once a
transcript passed half a model's context window — so the compaction layer is an
emergency valve, never a context-building step.

The summary **accompanies** the messages here; nothing is replaced. Replacement
is 08, and triage's switch is 09. Proving the summary works where no test
resists it comes first.

**Blocked by:** 01

**Decisions:** D2, D9, D11, D12

**Status:** ready-for-agent

- [ ] The summary carries the six fields D9 names, and is structured rather than
      free prose
- [ ] It records the first and last message it covers and the version that wrote
      it; raw messages are never deleted
- [ ] The summariser writes it when the room has said more since the last one,
      with the fraction-of-context-window gate removed
- [ ] A missing summariser configuration is loud at startup, as it already is
- [ ] The summary is stored plain and escaped once, at the section seam
- [ ] It reaches the extractor through the conversation slot of the memory
      section builder
- [ ] It is capped at the configured length, default six thousand characters
- [ ] The section is length-framed, and a summary that tries to forge its frame
      or a second key line inside the section describing the room does not
      succeed
- [ ] The scripted-transport seam is used; no test in this ticket touches the
      network
- [ ] Guards deleted once and watched go red
