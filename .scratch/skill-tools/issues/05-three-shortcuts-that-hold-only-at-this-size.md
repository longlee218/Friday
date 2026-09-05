# 05: Three shortcuts that hold only at today's library size

**What to build:** Search and the prompt sections behave sensibly on a library
that is empty and on one with a hundred skills, not only on the three that
ship today.

**Blocked by:** None (01–04 are done; this is work on top of what they landed)

**Status:** needs-triage — **one open decision, named below.** The other two
parts are decided and could be lifted into their own ticket if the first one
sits.

None of these three came from watching a real thread. They came out of
reviewing 01–04, which is worth saying: every one of them is a heuristic that
is *correct* at three skills, so nothing will report them until the library
grows, and then all three arrive at once. That is the argument for writing
them down now rather than the argument for building them now.

---

## The open decision: a query whose word form does not match

`search("vietnamese writing")` does not find `answer-in-vietnamese`, whose
description reads "How the operator writes to their team". Every token must
appear (ticket 04 fixed rank 4 to require that, and `any` is worse — four
ordinary words match everything on "the"), and `writing` is not in `writes`.

The rank is honest about what it does. The question is whether "honest" is
enough, and it is a real trade rather than an obvious fix:

- **A. A lowest rank for *most* tokens matched.** No dependency, no stemming.
  `vietnamese writing` gets 1 of 2 and lands below every genuine match, which
  the five-result cap then hides whenever real matches exist. Cheapest; also
  the vaguest thing to explain to the model in the tool description.
- **B. Crude suffix stripping** (`s`, `es`, `ing`, `ed`) on both sides before
  comparing. No dependency. Fixes this exact case and a common class of it;
  silently wrong on irregular forms, and the wrongness is invisible because a
  miss looks the same as an absent skill.
- **C. A real stemmer.** Correct, and against this repo's grain — runtime
  dependencies are deliberately few, and a stemmer is a large one to carry for
  a three-skill library.
- **D. Nothing.** The catalogue is *already in the prompt in full*, so a
  search miss is not a dead end: the agent has every name and description in
  front of it and search is a convenience over a list it can read. This is the
  option the other three have to beat, and at today's size it is winning.

**D is the current behaviour, and stays the behaviour until somebody picks
otherwise.** Whoever takes this ticket should not choose quietly.

- [ ] The decision is recorded here with its reason before any code
- [ ] Whatever is chosen, the tool description tells the model what the last
      rank actually does — 04 exists because a rank was advertised as token
      matching and was not

---

## The miss sentence lists every skill by name

`known()` renders every name, and both `fetch` and `search` end their miss
with it. At three skills that is a helpful nudge. At a hundred it is a
hundred names in a tool result — the catalogue's whole cost, paid again, by
the tool that exists so the catalogue does not have to grow.

Worse, it is paid on the *failure* path, which is the one the agent hits when
it is already unsure.

There is a second, sharper reason to change it, found while writing ticket 04:
the miss sentence containing every name is what let a bad test pass. It
asserted `name in answer`, and the miss it was meant to catch satisfied that
assertion. A miss that does not recite the library cannot do that again.

- [ ] A miss names what is available only while that is short; past a
      threshold it says how many there are and points at the catalogue the
      agent already has
- [ ] The threshold is one number in one place, not a rule each caller
      remembers
- [ ] `fetch`'s miss and `search`'s miss stay one phrase — 04 folded three
      copies into `known()`, and this must not unfold them
- [ ] A test pins that a large library's miss does not contain every name

---

## The prompt sections are driven by the catalogue, not by the tools

`build_input` computes `has_skills = bool(skills_catalogue)` and renders the
three tool sections off it. But the tools are wired on `skills is not None`.
A responder holding a **zero-skill** library therefore has `search_skills`,
`describe_skill` and `read_skill_file` and is told about none of them.

It errs the safe way — a silent tool is better than a described tool that
does not exist, which is the failure `memory_tool_system(available=...)` was
built to stop — so this is not urgent. It is still the same class of bug
inverted: the flag proxies a fact next to the one it means, and the two come
apart exactly when the library is empty.

An empty library is not hypothetical. It is what a fresh install has, and
`test_a_directory_that_does_not_exist_is_not_an_error` says so deliberately.

- [ ] Whether the three sections render is decided by whether the agent was
      given the tools, not by whether the catalogue has lines in it
- [ ] A responder built with an empty library either has no skill tools or is
      told about the ones it has — the two must agree, and which way they
      agree is the implementer's call to state
- [ ] `skill_system` keeps rendering nothing for an empty catalogue: there is
      genuinely nothing to list, and that is a different question from
      whether the tools exist
- [ ] A test covers the empty-library case, which nothing does today

---

## Out of scope

- Ranking by embeddings. Named out of scope in the spec and still is; the
  decision above is about the cheapest honest rank, not about a better index.
- Removing the catalogue from the prompt. It is the reason a search miss is
  survivable, and option D leans on it.
- Anything about `mutability` or `allowed_tools`. `allowed_tools` is still
  free-form and unvalidated by design (spec, Out of Scope) — a skill naming a
  tool the agent lacks reads as `(all)` from the agent's side.
