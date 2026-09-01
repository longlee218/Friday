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
- [x] Every agent is given the name and description of every skill, without their bodies
- [x] An agent can fetch a skill's body during a run and act on what it says
- [x] Fetching a skill that does not exist comes back as something the model can respond to, not an error that ends the run
- [x] A malformed skill file is reported at startup, naming the file, rather than failing mid-run
- [x] The prompt cost of the skill list grows with the *number* of skills, not their length
