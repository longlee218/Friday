# 03: Triage's prompt is classification and nothing else

**What to build:** Triage stops carrying a catalogue of skills it has never
fetched. 1,008 of its 2,272 system-prompt characters describe four tools it does
not use, and wiring those tools silently tripled its turn budget — on the
highest-volume path in the system.

Sequenced before 09 on purpose, though nothing technically gates it: both
tickets change what reaches the classifier and both must run the evaluation, so
running them separately keeps one variable per measurement. If accuracy moves,
this ordering says which change moved it.

**Blocked by:** None (can start immediately)

**Decisions:** D2, D23

**Status:** ready-for-agent

- [ ] Triage is built without the skill library, so it receives neither the
      catalogue section nor the four skill tools
- [ ] Triage's turn budget is what its configuration states, without the
      addition the tool families bought
- [ ] A test asserts the catalogue is absent from triage's instructions and
      fails if it comes back — no test pins this today, in either direction
- [ ] No other agent loses its catalogue
- [ ] Triage's tool schema is unchanged: a type and a confidence
- [ ] The evaluation is run against the frozen set, and accuracy, the confusion
      matrix and the threshold table are reported alongside the change
- [ ] The new guard is deleted once and watched go red
