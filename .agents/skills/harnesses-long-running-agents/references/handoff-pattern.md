# The handoff-artifact pattern, worked

## Minimum artifact set for a multi-session harness

- **`init.sh`** (or equivalent) — restores the working environment deterministically, so a new session doesn't have to guess what state things are in.
- **`progress.txt`** (or a progress log) — free-text narrative of what happened, what's still open, and anything the next session needs to know that doesn't fit a checklist field. Read first, every session.
- **A granular status checklist** (e.g. a JSON feature list with a `passes` field per item) — the agent may only *flip status flags*, never remove or rewrite entries. This constraint exists specifically to stop a session from quietly declaring something done by deleting evidence it wasn't, or narrowing scope to make a checklist look cleared.
- **Durable checkpoints as the real unit of progress** — commits (or equivalent) with descriptive messages, not just in-memory state that dies with the process. If the harness process itself dies mid-session, the checkpoint is what the next session actually resumes from, not the progress log's narrative alone.

## Why "may only flip a flag, never edit the list" matters

This single rule ("It is unacceptable to remove or edit tests") is doing real work: without it, an agent under pressure to show progress has an easy escape hatch — narrow the definition of done instead of doing the work. A checklist an agent can freely rewrite isn't a checkpoint, it's a suggestion. If you're building a similar mechanism, decide up front which fields a session may write and make everything else structurally read-only to it, not just documented as read-only.

## The session-start ritual

Every new session in the source's harness is explicitly instructed, before doing anything else: **read the git log and the progress file to get up to speed.** This is stated as an instruction, not assumed — the harness doesn't rely on a fresh session inferring that it should orient itself first. If you're building a comparable harness, put the equivalent instruction at the very top of whatever the session's first prompt is, ahead of the actual task description.

## Context resets vs. compaction — a decision that should be revisited, not just made once

| Approach | Trade | When it was chosen |
|---|---|---|
| **Full context reset between work units** | A clean slate, no leftover "context anxiety" from a long thread — but no continuity a model could otherwise use | Earlier model generation, in the source's own timeline |
| **In-conversation compaction (SDK-native)** | Continuity is preserved; the model retains implicit signal a hard reset throws away | Later model generation, once the team found the reset assumption no longer held |

The lesson isn't "compaction is better than resets" as a general rule — it's that **the choice was tied to a specific model's capability level**, and the team revisited it rather than treating the original decision as permanent. Any harness mechanism justified by "the model needs X" should be re-examined the same way periodically, not assumed correct forever because it was correct once.

## Generator/evaluator split — a minimal shape

```
generator agent:  produces a candidate output
                        │
                        ▼
evaluator agent:  independently scores it against explicit criteria
                   (skepticism has to be written into ITS prompt —
                    it does not follow automatically from being separate)
                        │
                        ▼
        pass → ship            fail → back to generator with feedback
```

The separation buys you an agent that isn't grading its own homework. It does not, by itself, buy you a *good* judge — a separate evaluator prompt that isn't deliberately tuned toward skepticism will still tend toward the same over-generous verdicts a generator gives itself, just with one extra hop.
