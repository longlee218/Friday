# SKILL.md spec details and this repo's own conventions

## Frontmatter field reference

```yaml
---
name: processing-pdfs          # required. lowercase, digits, hyphens only. max 64 chars.
                                # no XML tags. cannot be "anthropic" or "claude" or contain them
                                # as the whole name.
description: >                 # required. max 1024 chars. no XML tags.
  Extracts text and tables from PDF files and converts them to structured
  data. Use when the user provides a PDF and asks for its contents, wants
  a PDF summarized, or needs data pulled out of a scanned/digital document.
---
```

Nothing else is required. Anthropic's guidance is explicit that adding fields beyond `name`/`description` "rarely" earns its cost — resist the urge to add a `compatibility` or `version` field unless it's solving a real, current problem.

## The three-level model, restated as a cost model

Think of it as three different prices for the same information:

1. **Metadata (name + description)** — paid on *every single turn*, for every installed skill, forever, whether or not the skill is relevant to what's happening. This is why a description needs both specificity (so it fires when it should) and restraint (so its constant cost stays small).
2. **Body** — paid once per triggering, and only while the skill is in play. This is where "don't repeat what Claude already knows" earns its keep — every line here is being paid for by every future trigger, not just the one being written for right now.
3. **Reference files** — paid only if actually opened. This is the cheapest tier, and it's specifically for the material that's important *sometimes* but would be wasted context *most* of the time: extended examples, large checklists, format specs, code samples.

Get the assignment of content to level wrong in either direction and it costs something concrete: too much in the description → wasted tokens on every turn regardless of relevance; too much crammed into the body → wasted tokens on every trigger even when only part of it is relevant; too much left in the body that should be a reference → the same problem, at a smaller scale, for less-common branches of the skill's own logic.

## The one-hop reference rule, and why it's not just a style preference

Claude Code reads a reference file linked from `SKILL.md` in full, but a file reached by following a link *from that reference file* may only be partially read (e.g. via `head -100`). This isn't a stylistic nicety — content nested two hops from the entry point can be silently truncated with no error, which means a skill author who nests reference files can ship a skill that appears to work in testing (if the truncated part wasn't touched) and then quietly misses content in production. Keep every reference file a direct child of `SKILL.md`, never of another reference file.

## This repo's own package-manager layer

Two directories exist, and they are not the same thing:

- **`.agents/skills/`** — the tracked source-of-truth library. Every skill's actual files live here.
- **`.claude/skills/`** — what Claude Code actually scans at runtime. Per the official discovery mechanics, Claude Code only reads `.claude/skills/<name>/SKILL.md` (at enterprise/personal/project/nested precedence). In this repo, every entry under `.claude/skills/` should be a **symlink** into `.agents/skills/…` — e.g. `codebase-design -> ../../.agents/skills/codebase-design`.

A `skills-lock.json` at the repo root is the installer's manifest: each entry records a `source` (a GitHub repo), `sourceType`, `skillPath`, and a `computedHash`. Treat this as a real package manager, not ad hoc copying — a new skill added by hand (rather than through the installer) should still follow the same symlink convention to stay consistent with everything the installer manages.

**A concrete failure mode to check for**: at least one skill in this repo (`prompt-engineering-patterns`, sourced from a plugin-nested GitHub path) was installed as two *independent, real* directory copies — one in `.agents/skills/`, one in `.claude/skills/` — instead of the second being a symlink to the first. Both were byte-identical and both uncommitted, which is the signature of an installer that materializes a full copy for some source types instead of symlinking. If you're adding a skill by hand, verify with `file .claude/skills/<name>` that it reports as a symlink, not a directory, before moving on.

## Checklist: is this new content body, reference, or should it not exist at all

- [ ] Would every trigger of this skill need this information? → body.
- [ ] Would only *some* triggers need it, or is it detail beyond what a first read requires? → a reference file, linked once, one hop deep.
- [ ] Does Claude already know this without being told (general programming knowledge, well-known conventions)? → cut it; every retained line is paid for by every future trigger.
- [ ] Is this actually about *when to use the skill* rather than *what to do once triggered*? → it belongs in `description`, not the body — misplacing "when" information into the body means it never influences the triggering decision at all.
