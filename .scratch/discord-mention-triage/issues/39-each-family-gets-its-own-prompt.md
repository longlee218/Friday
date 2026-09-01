# 39: Each family gets its own prompt

**What to build:** Four kinds of agent, four prompts. Nothing an agent is told
is there because a different agent needed it.

**Blocked by:** None (can start immediately)

**Status:** done

## Why

One bundle class renders every prompt, and every agent gets the same closing
line — including a precedence rule for three sections most of them are never
passed:

    Follow the instructions in each section. Section precedence:
    channel_overrides > channel_derived > channel_base;
    task-specific instructions win over channel defaults.

Triage is told how to resolve a conflict between sections it does not receive.
Its whole output is which of four tools it called and a number.

The persona has the same problem from the other side. It is one file with three
modes, and the modes cut across the families: an extractor and an analysis node
share a mode while doing unrelated jobs. Choosing a mode was already shown to
go stale — triage carried the wrong one for several commits after its job
changed, and 79% of the highest-volume prompt in the system was a description
of how to write replies, sent to something that never writes one.

## The four

| Family | Receives |
|---|---|
| Triage | the turn's messages, and nothing else |
| Extraction | the field schema, and everything the reporter has said |
| DAG node — including the analysis node | the node's own instructions, what earlier nodes found, its tools |
| Responder — including the node that composes the reply | identity, the operator's voice, the channel's register, real examples, the conversation, the task's parameters, and what to say |

The analysis node writes a `cause` a person reads, which is why it looks like a
writer. It is not: a diagnosis needs to be *right* before it needs to be
natural, and the composer rewords it afterwards.

## What stays shared, and why that is not a contradiction

**Text and shape** split per family. **Mechanism** does not.

Escaping at the boundary is a security seam, not a style. There were two
renderers for the skill catalogue and only one escaped, so a skill described as
`harmless</skills>` closed its own section and everything after it read as
instructions. That was fixed by deleting the second renderer, not by adding
escaping to it. Four copies would be four chances for one to forget.

## Acceptance criteria

- [x] Each family's prompt is assembled by something that knows only that
      family; no closing line, identity or section reaches an agent that has no
      use for it
- [x] `PERSONA.md` is organised by family rather than by mode, and the one
      sentence that is genuinely common — *you are not Long, and everything you
      write goes out under his name* — is written once
- [x] `persona: full | language | none` is gone from `config.yaml`
- [x] Values that reach a prompt are still escaped at one seam, and a test
      fails if a second renderer appears
- [x] A test prints all four prompts and asserts no family's text appears in
      another's
- [x] Triage's prompt contains nothing about sections, channels or voice

## What it came to

| | before | after |
|---|---|---|
| triage prompt | 2030 chars, 79% persona | 418 chars, the job and the examples |
| shared wrapper | every agent | none |
| `persona:` in config | per agent, three modes | gone |
| `PERSONA.md` | three modes cutting across jobs | two families: Responder, Node |

Escaping stays in one module, and a grep test fails if a second one appears.
The precedence sentence for the channel sections moved into the responder's
own instructions — the one family that receives them. `dag_compose` will need
it too once ticket 40 hands it the sections.
