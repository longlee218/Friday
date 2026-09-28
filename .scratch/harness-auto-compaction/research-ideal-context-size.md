# What context size should an agent loop compact at?

Research note for board `harness-auto-compaction`. Written 2026-09-28; every
URL below was fetched that day. Local facts were checked against the working
tree and `.venv` (`pydantic-ai-slim 2.46.0`) on the same day. Sections 1–5 are
facts with sources. Section 6 is judgment and is labelled as such.

The question: at what context size (tokens) should a Friday agent loop (e.g.
diagnose: reading logs and code with tools) start compacting, and how should
auto-compaction work?

## 1. The model Friday actually runs

The working-tree `config.yaml` sets `model: "deepseek/deepseek-v4.1-flash"`
through OpenRouter for every agent (triage, responder, diagnose). Line 54's
comment says diagnose "Reads logs + code, so 1M context helps".
`friday/kernel/config.py:148` defaults `AgentConfig.context_window = 128_000`.
Nothing else under `friday/` reads that field (grep, 2026-09-28).

### DeepSeek-V4.1-Flash (primary)

| Source | Context | Max output |
| --- | --- | --- |
| DeepSeek API docs, "Models & Pricing" (`deepseek-flash` = DeepSeek-V4.1-Flash) — https://api-docs.deepseek.com/quick_start/pricing | 1M | 384K maximum |
| OpenRouter models API, `deepseek/deepseek-v4.1-flash` (created 2026-09-10) — https://openrouter.ai/api/v1/models | `context_length` 1,048,576; top provider 1,040,000 | top provider `max_completion_tokens` 384,000 |
| Hugging Face model card — https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash | "contexts of up to one million tokens" | recommends `max_tokens` ≥ 256K (a setting, not a limit) |

So the "1M context" comment in `config.yaml` is documented, by DeepSeek and by
OpenRouter. The 128K code default is not the model's window. It is a
conservative number of Friday's own.

The model card reports LongBench-V2 EM 45.2 (1-shot). No vendor source gives a
RULER or MRCR-style curve for this model, so there is no first-party data on
where its quality starts to fall below 1M.

### MiniMax-M3 (secondary; not the configured model)

- MiniMax API docs, "Model Invocation": `MiniMax-M3` context window
  1,000,000. That page does not state a max output figure —
  https://platform.minimax.io/docs/guides/text-generation
- MiniMax blog (2026-06-01): "up to 1M tokens"; "calls with ≤512K input tokens
  are billed at the standard rate". It gives no universal max output —
  https://www.minimax.io/blog/minimax-m3
- OpenRouter `minimax/minimax-m3`: `context_length` 1,048,576, top provider
  524,288, `max_completion_tokens` 512,000 (a third-party figure, not
  MiniMax's own).

## 2. Evidence that quality drops as context grows

| Source | Date | What it says |
| --- | --- | --- |
| Liu et al., "Lost in the Middle" (TACL) — https://arxiv.org/abs/2307.03172 | v1 2023-07-06, v3 2023-11-20 | Performance is highest when the relevant information is at the start or end of the input, and drops a lot when it is in the middle. This holds for explicitly long-context models too. |
| Hsieh et al. (NVIDIA), "RULER" — https://arxiv.org/abs/2404.06654 | v3 2024-08-06 | 17 models, 13 tasks. "While these models all claim context sizes of 32K tokens or greater, only half of them can maintain satisfactory performance at the length of 32K." Near-perfect needle-in-a-haystack scores hide large drops on harder tasks. |
| Chroma, "Context Rot" — https://www.trychroma.com/research/context-rot | 2025-07-14 | 18 models (GPT-4.1, Claude 4, Gemini 2.5, Qwen3). Performance "varies significantly as input length changes, even on simple tasks". Distractors make the decline worse, and 4 hurt more than 1. On LongMemEval, a focused prompt (~300 tokens) beats the full prompt (~113K tokens) for every model, and the gap stays with thinking modes on. |
| Anthropic, "Effective context engineering for AI agents" — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents | 2025-09-29 | Names "context rot": recall falls as tokens grow. Treats context as a finite "attention budget" (n² pairwise relationships). Gives no token threshold. |

Concrete thresholds in these sources: RULER's 32K, where half the models
claiming ≥32K fail, and Chroma's ~113K full prompt versus ~300 focused. None
gives a universal "compact at N" number. Every source says that less relevant
context is better and that the advertised window overstates the usable one.

## 3. What production harnesses use

| Harness | Trigger | What it keeps | Source |
| --- | --- | --- | --- |
| Claude Code auto-compact | Default: at the model's context limit. For native 1M models on the Anthropic API, ~967K. 200K-window sessions compact at the 200K boundary. Configurable 100K–1M via `/autocompact`, `--autocompact`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`. | Startup content (reloaded), "a structured summary of the entire conversation, the files modified most recently, and the body of each skill you invoked" (≤5,000 tokens per skill). Custom focus through `/compact <instructions>` or a "Compact instructions" section in CLAUDE.md. | https://code.claude.com/docs/en/model-config ; https://code.claude.com/docs/en/context-window ; https://code.claude.com/docs/en/costs |
| Anthropic API, threshold compaction (`compact_20260112`, beta) | Default `{"type":"input_tokens","value":150000}`, minimum 50,000 | The server summarises older turns into a compaction block. `pause_after_compaction` lets you re-insert recent turns verbatim. `instructions` replaces the default prompt, which asks for "state, next steps, learnings" in a `<summary>` block. | https://platform.claude.com/docs/en/build-with-claude/compaction-threshold ; overview: https://platform.claude.com/docs/en/build-with-claude/compaction |
| Anthropic API, context editing `clear_tool_uses_20250919` (beta `context-management-2025-06-27`) | Default trigger 100,000 input tokens | Keeps the last 3 tool uses by default. Options: `clear_at_least`, `exclude_tools`, `clear_tool_inputs` (default false). | https://platform.claude.com/docs/en/build-with-claude/context-editing |
| Anthropic results for context editing | 2025-09-29 | Context editing alone: +29%. With the memory tool: +39%. On a 100-turn web-search eval it cut tokens by 84% and finished runs that otherwise ran out of context. | https://claude.com/blog/context-management |
| Anthropic compaction guidance | 2025-09-29 | Keep "architectural decisions, unresolved bugs, and implementation details" and drop "redundant tool outputs". Claude Code continues with the summary plus "the five most recently accessed files". Tool-result clearing is the "lightest touch". | engineering post above |
| OpenAI Codex CLI | `model_auto_compact_token_limit`. Unset means 90% of the context window, and a set value is clamped to ≤90%. `effective_context_window_percent` defaults to 95. | `compact_prompt` overrides the summary prompt. `model_auto_compact_token_limit_scope` is `total` (default) or `body_after_prefix`. | https://learn.chatgpt.com/docs/config-file/config-reference ; source `codex-rs/protocol/src/openai_models.rs` (openai/codex main, read 2026-09-28) |
| OpenAI Responses API compaction | `context_management=[{"type":"compaction","compact_threshold": N}]`. The docs give no default; the example uses 200,000. Also a stateless `/responses/compact`. | An opaque, encrypted compaction item | https://developers.openai.com/api/docs/guides/compaction |
| OpenAI Agents SDK | `OpenAIResponsesCompactionSession` compacts after each turn when `should_trigger_compaction` holds. The docs state no default number. | Rewrites the session history | https://openai.github.io/openai-agents-python/sessions/ |

The pattern: harnesses built for a person's long interactive session (Claude
Code, Codex) compact late, at 90–97% of the window. API defaults meant for
agent loops sit at 100K (tool-result clearing) to 150K (summarisation),
whatever the model's window.

## 4. Pydantic AI's mechanism (installed: `pydantic-ai-slim 2.46.0`)

- **API shape.** In 2.46.0 history processing is a capability:
  `Agent(..., capabilities=[ProcessHistory(fn)])`, from
  `pydantic_ai.capabilities`. `Agent.__init__` has no `history_processors`
  parameter; the only related parameter is `capabilities` (checked with
  `inspect.signature`). `fn` is `(messages) -> messages` or
  `(ctx: RunContext, messages) -> messages`, sync or async
  (`capabilities/process_history.py`). It runs in `before_model_request` and
  replaces the messages sent on each model request. Docs:
  https://github.com/pydantic/pydantic-ai/blob/main/docs/message-history.md ,
  `docs/capabilities/process-history.md` (Context7 `/pydantic/pydantic-ai`).
- **Trigger signal.** `RunContext.context_window_used` gives the fraction of
  the window used, computed as the latest response's `usage.total_tokens`
  divided by `model.context_window` (`_run_context.py:448`). It returns
  `None` when the window is unknown or before the first response. The
  official example compacts when `used > 0.8` and keeps "the most recent
  complete user turn, including any later tool calls and returns".
- **Caveat for Friday.** `model.context_window` comes from the provider
  profile, then genai-prices, then the user `profile=`. For
  `OpenAIChatModel('deepseek/deepseek-v4.1-flash',
  provider=OpenAIProvider(openai_client=...))`, which is how
  `friday/kernel/harness/harness.py:880` builds it, the value is **`None`**.
  So `context_window_used` is `None` too, unless Friday passes
  `profile={'context_window': ...}`, which was checked to set it. The other
  option is a trigger on `ctx.usage` or the last `ModelResponse.usage`.
- **Summarisation.** The docs show a secondary cheaper `Agent` summarising the
  oldest messages inside a processor and returning
  `summary.new_messages() + messages[-1:]`. `OpenAICompaction` and
  `AnthropicCompaction` exist (`models/openai.py:4849`,
  `models/anthropic.py:3028`), but they target the OpenAI Responses API and
  the Anthropic API. Neither fits Chat Completions through OpenRouter.
- **Pairing caveat (docs, verbatim).** "When slicing the message history, you
  need to make sure that tool calls and returns are paired, otherwise the LLM
  may return an error." It links
  https://github.com/pydantic/pydantic-ai/issues/2050 (2025-06-21). The
  referenced comment (2025-06-30) points to a community example processor and
  adds nothing normative. In practice, cut only at a `ModelRequest` that holds
  a `UserPromptPart` and no `ToolReturnPart`/`RetryPromptPart`, as the
  official example does, or replace a tool return's content instead of
  deleting the part.

## 5. What is not known

- No first-party long-context quality curve exists for DeepSeek-V4.1-Flash, or
  for MiniMax-M3. Where this model degrades is unmeasured.
- No source gives an "ideal" absolute context size. The numbers above are
  harness defaults, not measured optima.
- Chroma's and RULER's model sets do not include this model.

## 6. Recommendation (judgment, not a sourced fact)

Hypothesis: **for Friday's agent loops on DeepSeek-V4.1-Flash, compact at
about 100K input tokens (~10% of the 1M window), and do not use a percentage
of the window.**

Reasons:
- The window is 1M, but the evidence (RULER 32K, Chroma ~113K) and the API
  defaults (100K clearing, 150K summarising) all sit in the 32K–150K range.
  Codex's and Claude Code's 90–97% suit a person's interactive session, not a
  diagnose run that has a deadline and is billed by the token.
- An absolute token trigger works even though Friday's model reports no
  `context_window` (§4).

How it should work, cheapest step first:
1. **Trigger** on the last `ModelResponse.usage` input tokens ≥ 100K, checked
   in a `ProcessHistory(fn)` before each request.
2. **Clear old tool results first.** Replace the content of all but the last
   3 `ToolReturnPart`s with a stub such as "[cleared: <tool> <args>]", and
   keep the parts, so call/return pairs survive. This mirrors
   `clear_tool_uses` defaults.
3. **Summarise only if still ≥ ~150K.** A cheap model call writes a structured
   summary (task, findings so far, open hypotheses, files and log lines
   already read, next step). Keep the system prompt, the original task
   message and the last complete user turn verbatim, and cut only at a
   user-prompt boundary.
4. **Keep `AgentConfig.context_window` as the hard cap.** Treat the 128K
   default and the 100K trigger as one decision. Either raise the default to
   the documented 1M and keep the trigger at 100K, or leave it and set the
   trigger below it.

How to test it: run the diagnose eval (once it can score; see memory note
"build-the-loop scoring deferred") at triggers of 50K, 100K and 200K, and
compare accuracy and tokens per run. Until then, 100K is a starting point, not
a finding.
