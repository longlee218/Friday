---
name: claude-agent-skills
description: Apply Anthropic's official Agent Skills spec when writing or reviewing a SKILL.md — required frontmatter and its exact character/naming constraints, the three-level progressive disclosure model (metadata always loaded, body loaded on trigger, references loaded on demand), and Anthropic's own description-writing and body-length best practices. Use this whenever creating a new skill, editing an existing SKILL.md's frontmatter or structure, deciding whether content belongs in the body or a reference file, or troubleshooting a skill that won't trigger (or triggers when it shouldn't) — this is the platform spec and Anthropic's own checklist, distinct from writing-for-agents' general theory of context pointers across skills/AGENTS.md/CLAUDE.md.
---

# Claude Agent Skills

Anthropic's spec for modular, filesystem-based capability packages Claude loads on demand rather than always keeping in context (`platform.claude.com/docs/.../agent-skills/`). This is the mechanical, platform-level reference — for the general theory of *why* a pointer's wording decides whether an agent reaches material at all, see the `writing-for-agents` skill; this one is about the specific rules the SKILL.md format itself enforces.

## Required structure

A skill is a directory with `SKILL.md` at the top, plus optional `scripts/`, `references/`, `assets/`. The **only required frontmatter fields are `name` and `description`** — nothing else is required, and nothing else should be added out of habit.

- `name`: max 64 characters, lowercase letters/numbers/hyphens only, no XML tags, no reserved words (`anthropic`, `claude`). Prefer a gerund-style name (`processing-pdfs`, `analyzing-spreadsheets`) over a vague one (`helper`, `utils`) or an overly generic one (`documents`, `data`).
- `description`: non-empty, max 1024 characters, no XML tags. **Must include both what the skill does and when Claude should use it** — this single field is the entire triggering mechanism (see below), so treat every word in it as load-bearing.

## Progressive disclosure — three levels, three different costs

| Level | What loads | When | Budget |
|---|---|---|---|
| 1. Metadata | `name` + `description` only | Always, from startup | ~100 tokens per skill |
| 2. Instructions | The `SKILL.md` body | Once the description matches the current request | Under ~5k tokens; keep the body under 500 lines |
| 3. Resources/code | Reference files, scripts, assets | Only when actually read or executed | No practical limit — unread files cost nothing |

The consequence that actually matters day to day: **every skill's `description` is permanent context load on every single turn, for every skill installed**, whether or not it ever triggers. This is why a bloated or vague description is worse than a merely mediocre one — it's not consulted once, it's paid for constantly.

## Skills are not a tool-calling mechanism

Unlike a tool or an MCP server, a skill is read via ordinary filesystem/bash access, not invoked with structured arguments. Discovery is purely description-based — Claude pattern-matches the current request against the always-loaded description strings to choose among potentially 100+ installed skills, with no separate registry or embedding search. A skill's instructions *can* reference MCP tools by fully-qualified `ServerName:tool_name` names, but the skill itself is discovered by description text alone.

## Writing a description that actually triggers

Anthropic's own best-practices, stated directly:

- **Always third person.** "Processes Excel files and generates reports," never "I can help you..." or "You can use this to...". Their stated reason: the description is injected into the system prompt, and an inconsistent point of view causes discovery problems.
- **Be specific — name both what and when**, including the literal trigger phrases a user would actually type, not a generic phrase like "Helps with documents."
- **Build evaluations before writing extensive documentation.** Write the skill from *observed* triggering gaps, not imagined ones, and test across the model sizes you'll actually deploy on.

## Structural rules for the body

- Keep the body under 500 lines; once you're approaching that limit, split the overflow into a reference file with a clear pointer to it, rather than letting the body keep growing.
- **References must stay one hop deep from `SKILL.md`.** Claude may only partially read a file reached through a second hop (a reference file pointing at another reference file), so content nested two links away can silently go unseen.
- Assume Claude is already very smart — don't explain things it already knows. Every token in the loaded body competes with the actual conversation.

Full frontmatter constraints, this repo's own two-directory skill-package convention, and the `prompt-engineering-patterns` installer inconsistency are documented in [`references/skill-md-spec.md`](references/skill-md-spec.md). Full citations: `docs/research/agentic-system-design/claude-agent-skills.md`.

## Applying this to Friday

**Scope discipline is the biggest risk for a new skill in this repo.** `domain-modeling` already owns codebase-vocabulary/CONTEXT.md territory, `codebase-design` already owns deep-module/seam vocabulary, and `writing-for-agents` already owns the pointer-wording theory for skills generally — a new skill whose description drifts into any of those areas will compete for the same trigger phrases, and Claude will pick one description over another based on wording, not intent. Before writing a new skill's description, check it against the descriptions of skills already installed in `.agents/skills/` for overlap.

**Given how large and decision-dense `CLAUDE.md` already is**, a skill meant to hand an agent this repo's own architectural vocabulary should follow the same shape `domain-modeling` and `codebase-design` already use — a short body naming the core categories, pointing one hop out to a reference file per topic — rather than inlining large chunks of `CLAUDE.md` wholesale into the body, which would blow past the 500-line target immediately.
