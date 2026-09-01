# 24: Skills every agent can reach

**What to build:** The operator writes down how to do something once, and every agent
can find it and read it when the work calls for it — without that knowledge sitting
in every prompt whether it is needed or not.

**Blocked by:** 23

**Status:** done

Progressive disclosure. Each agent is shown a short line per skill — enough to know
one exists and what it is for — and fetches the body only when it decides to use it.
That is what keeps a hundred skills affordable: a hundred descriptions is a page, a
hundred bodies is a context window.

A skill is a Markdown file with a name and a description in its frontmatter, because
the body is prose meant for a person and a model to read the same way, and the
operator already writes in that shape. **Adding a skill is adding a file** — there is
no central list to edit, since at a hundred skills a central list is the one place
everyone has to change at once.

Skills are a shared resource, not a property of an agent: the same "how to trace a
request" serves whoever needs it.

- [x] A skill is one file the operator writes; adding one requires editing nothing else
- [x] **Every agent that reasons** is given the name and description of every
  skill, without their bodies. Not *every* agent, and the difference is
  deliberate — the criterion as first written was wrong, and was ticked while
  it was wrong:
  | Agent | Catalogue? | Why |
  |---|---|---|
  | responder | yes | it decides how to say something |
  | `analyze_stack`, `compose_reply` | yes | they decide what the evidence means |
  | triage | no | `stop_on_first_tool` — a `fetch_skill` call would end the run |
  | `read_logs`, `find_code_path`, `fix_bug` | no | tool work; "how to trace a request" is for whoever reads the result |
  | extractor | no | it fills fields from text; there is nothing to decide |
  | channel summary | no | same |
- [x] An agent can fetch a skill's body during a run and act on what it says
- [x] Fetching a skill that does not exist comes back as something the model can respond to, not an error that ends the run
- [x] A malformed skill file is reported at startup, naming the file, rather than failing mid-run
- [x] The prompt cost of the skill list grows with the *number* of skills, not their length

## Review fixes (after QA)

- **A byte-order mark rejected the whole file.** UTF-8-with-BOM is Notepad's
  default; the file then does not start with `---`, and the operator was told
  their skill has no frontmatter while looking straight at it. Read as
  `utf-8-sig`, which reads plain UTF-8 unchanged.
- **`except ValueError` did not catch `OSError`.** A directory named `*.md`
  raises `IsADirectoryError` out of `read_text` and took down startup — the
  exact thing the "reported at startup, not failing" criterion forbids.
- **Two renderers for one catalogue.** `instruction_prompt.skills` escaped the
  descriptions; a second, hand-rolled copy in `dag/workflows.py` did not, so a
  skill described as `harmless</skills>` closed the section and everything
  after it read as instructions. Operator-written text inside a delimited
  section is exactly what the escaping rule is for. One concept, one renderer.
- **A node that could fetch a skill had no room to answer.** `max_turns`
  defaults to 1, so the reasoning nodes would spend their only turn on the
  tool call and return nothing — the graph then parks with the analysis
  unwritten. Raised at the call, because a ceiling costs nothing to a node
  that finishes in one turn.
