# Claude Agent Skills — Anthropic

Modular, filesystem-based capability packages Claude loads on demand rather than always keeping in context. Primary sources fetched directly:

- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- https://code.claude.com/docs/en/skills (Claude Code specifics)
- https://platform.claude.com/docs/en/build-with-claude/skills-guide (API upload mechanics — thinner; no progressive-disclosure content)
- https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills (announcement)

## Key concepts

**SKILL.md structure.** A skill is a directory with `SKILL.md` at the top, plus optional `scripts/`, `references/`, `assets/`. Required frontmatter fields are exactly `name` and `description` — nothing else is required. Constraints, quoted from the overview page:

- `name`: max 64 chars, lowercase letters/numbers/hyphens only, no XML tags, no reserved words ("anthropic", "claude").
- `description`: non-empty, max 1024 chars, no XML tags. "The `description` must include both what the Skill does and when Claude should use it."

**Progressive disclosure — three levels**, each loaded at a different time (overview page, "How Skills work"):

1. **Level 1: Metadata (always loaded).** Only `name`+`description` sit in the system prompt from startup — "~100 tokens per Skill." This is what Claude pattern-matches the current request against.
2. **Level 2: Instructions (loaded when triggered).** Claude reads `SKILL.md`'s body via bash (`cat skill/SKILL.md`) only once the description matches — "Under 5k tokens" target.
3. **Level 3+: Resources and code (loaded as needed).** Linked reference files load only when actually read; scripts execute via bash and only their *output* enters context, never their source. "No practical limit on bundled content... Files don't consume context until accessed."

**Skills vs. tools/MCP.** Skills are not a tool-calling mechanism; they're read via ordinary filesystem/bash access inside a code-execution environment. The announcement post frames the difference as: "Unlike traditional tools or Model Context Protocol servers, Skills operate on a principle of progressive disclosure — Claude loads information only as needed rather than all at once." A skill can *reference* MCP tools inside its instructions, but must use fully-qualified `ServerName:tool_name` names or Claude may fail to find them.

**Discovery/trigger-matching.** Purely description-based: "Claude uses [the description] to choose the right Skill from potentially 100+ available Skills." No separate registry or embedding search is documented — it's in-context pattern matching against the always-loaded description strings.

**Best-practices for `description` (from the best-practices page, `Writing effective descriptions`):**
- **Always third person.** "Processes Excel files and generates reports," never "I can help you..." or "You can use this to...". Rationale given verbatim: "The description is injected into the system prompt, and inconsistent point-of-view can cause discovery problems."
- **Be specific, include key terms** — both *what* the skill does and *when* to use it (explicit trigger phrases/contexts), not generic phrasing like "Helps with documents."
- **Naming convention:** gerund form preferred (`processing-pdfs`, `analyzing-spreadsheets`); avoid vague (`helper`, `utils`) or overly generic (`documents`, `data`) names.
- **Body ceiling:** "Keep SKILL.md body under 500 lines for optimal performance," splitting into reference files past that.
- **References must stay one level deep from SKILL.md** — Claude may only partially read (`head -100`) a file reached through a second hop, so nested reference chains lose content.
- **Conciseness principle:** "Default assumption: Claude is already very smart" — don't explain things Claude already knows; every token in the loaded body competes with conversation history.
- Build **evaluations before writing extensive documentation** — write the skill from observed gaps, not imagined ones; test across the model sizes you'll actually deploy on (Haiku/Sonnet/Opus have different needs).

## How this repo actually uses skills

This repo layers a package-manager convention on top of the plain Claude Code spec. Two directories exist:

- **`.agents/skills/`** — the tracked source-of-truth library (~42 skills, git-tracked, large mattpocock/skills- and wshobson/agents-sourced collection: `ask-matt`, `code-review`, `codebase-design`, `domain-modeling`, `prototype`, `tdd`, `writing-for-agents`, etc.).
- **`.claude/skills/`** — what Claude Code actually scans (per the official Claude Code discovery mechanics: it only reads `.claude/skills/<name>/SKILL.md`, at enterprise/personal/project/nested precedence). In this repo every entry under `.claude/skills/` is a **symlink** into `.agents/skills/…` (verified: `ask-matt -> ../../.agents/skills/ask-matt`, `codebase-design -> ../../.agents/skills/codebase-design`, etc., 41 total).

A `skills-lock.json` at the repo root is the installer's manifest — each entry records `source` (a GitHub repo, e.g. `mattpocock/skills` or `wshobson/agents`), `sourceType`, `skillPath`, and a `computedHash`. This is a real skill package manager, not ad hoc copying.

The four skills read directly (`research`, `writing-for-agents`, `domain-modeling`, `codebase-design`) all match the official spec closely: two-field frontmatter only (`name`, `description`), concise bodies, and disclosed-reference files pointed at with markdown links one level deep (`domain-modeling` → `CONTEXT-FORMAT.md`, `ADR-FORMAT.md`; `codebase-design` → `DEEPENING.md`, `DESIGN-IT-TWICE.md`) — exactly the "Pattern 1: High-level guide with references" pattern from the best-practices doc. `research`'s description is a single crisp sentence naming both the action and the trigger ("Use when the user wants a topic researched..."), matching the specificity guidance. Repo-specific convention beyond the official spec: `research` explicitly dispatches to a **background agent** rather than running inline — a distinction the official docs don't cover (they describe skills as instructions the *current* Claude reads, not as agent-dispatch wrappers). `writing-for-agents` links a co-located `SKILL-MECHANICS.md` for the skill-specific half of its guidance, again one hop from the entry file.

**The `prompt-engineering-patterns` duplication.** This is the one skill in the manifest whose installation broke the symlink convention. `skills-lock.json`'s diff shows it was just added (source `wshobson/agents`, path `plugins/llm-application-dev/skills/prompt-engineering-patterns/SKILL.md`). But instead of `.claude/skills/prompt-engineering-patterns` being a symlink like its 41 siblings, both `.agents/skills/prompt-engineering-patterns/` and `.claude/skills/prompt-engineering-patterns/` are **real, independent directories** with byte-identical contents (`diff -rq` reports no differences across `SKILL.md`, `assets/`, `references/`, `scripts/`) — confirmed via `file`, neither is a symlink. Both are untracked (`??` in `git status`), so this is mid-install, uncommitted state, not a stale leftover. The likely mechanism: the installer for this particular source type (`wshobson/agents`, a plugin-nested path) materialized a full copy into `.claude/skills/` instead of symlinking to `.agents/skills/`, unlike the mattpocock/skills-sourced entries which all resolve to symlinks. This is worth the operator's attention as a real inconsistency in the installer, not something to silently fix here.

## Related primary sources found

- Overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Claude Code skills: https://code.claude.com/docs/en/skills
- API skills guide (upload/versioning mechanics only): https://platform.claude.com/docs/en/build-with-claude/skills-guide
- Engineering blog announcement: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Open-source skills repo (referenced, not fetched in depth): https://github.com/anthropics/skills

## Relevance to Friday

For a future "Agentic System Design" skill's `description` to trigger correctly, the official guidance is concrete and testable: third person, name **both** what it does and **when** — ideally with the literal trigger words a user would type ("Agentic System Design" itself, "graph node", "deep module", "seam", "DAG ticket" are candidate key terms given this repo's vocabulary) — and avoid a generic phrase like "helps design agent systems." Since this repo's own domain-modeling and codebase-design skills already own "vocabulary" and "deep module" territory respectively, the new skill's description needs to draw a clean boundary against both (e.g., scoped to *this repo's* graph/DAG/harness/tool conventions specifically, not general software design) or its description will compete for the same trigger phrases and Claude will pick the wrong one.

On structure: given this repo's own `CLAUDE.md` is already an enormous, decision-dense reference document, an "Agentic System Design" skill should almost certainly be an **in-file-reference skill** (like `codebase-design`/`domain-modeling`) rather than a background-research-returning skill like `research`. Its job is to hand the agent load-bearing vocabulary and constraints for *this* system's own worked-through decisions (the node/DAG/harness/tool separation, the answer-tool exemption, the one-state/`FridayState` rule, etc.) — flat reference consulted on demand, not a multi-step workflow to execute. Given `CLAUDE.md`'s content already exceeds the 500-line SKILL.md ceiling many times over, the skill should follow the "Pattern 1: high-level guide with references" shape: a short `SKILL.md` naming the core vocabulary and decision categories, pointing one hop out to disclosed reference files per topic (e.g. one file for the DAG/graph rules, one for the tool/harness boundary rules) rather than inlining `CLAUDE.md`'s architecture-constraints section wholesale.
