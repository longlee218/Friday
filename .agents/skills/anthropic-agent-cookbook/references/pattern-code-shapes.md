# Pattern code shapes, condensed

## `chain`, `route`, `parallel` (basic_workflows.ipynb)

```python
def chain(input, prompts: list[str]) -> str:
    result = input
    for prompt in prompts:
        result = llm_call(f"{prompt}\nInput: {result}")
    return result

def route(input, routes: dict[str, str]) -> str:
    # one classification call picks a key from routes.keys() — HARDCODED set
    selection = llm_call(routing_prompt(input, routes.keys()))  # <reasoning>/<selection>
    return llm_call(routes[selection], input)

def parallel(prompt, inputs: list[str], n_workers=3) -> list[str]:
    # same prompt fanned out over a thread pool; no synthesis step
    with ThreadPoolExecutor(n_workers) as ex:
        return list(ex.map(lambda x: llm_call(prompt, x), inputs))
```

## `orchestrator_workers.ipynb` — what it does and doesn't do

Does: one call returns `<analysis>` + a list of `<task><type>/<description></task>` entries, decided at runtime from the specific input — this *is* the pattern's defining property.

Doesn't: run the resulting tasks in parallel (the reference loop is sequential — the notebook flags this as a limitation, not a design choice worth keeping), or synthesize the workers' results into one final answer (left as an unimplemented "next step"). Treat both as gaps to fill, not as things the pattern name already promises.

## `evaluator_optimizer.ipynb` — the loop, with the missing piece marked

```python
def loop(task, context=""):
    thoughts, result = generate(prompt, task, context)
    while True:
        evaluation, feedback = evaluate(eval_prompt, result, task)
        if evaluation == "PASS":
            return result
        context += f"\nPrevious attempt: {result}\nFeedback: {feedback}"
        thoughts, result = generate(prompt, task, context)
    # ^ NOTE: no iteration cap here. This is the actual reference shape —
    # any bounded version of this loop is something you add, not something
    # that ships with the pattern.
```

## `async_multi_agent_orchestration.ipynb` — the two variants

```python
# Variant 1: fixed team, decided in code ahead of time
peers = ["researcher", "writer", "critic"]
await asyncio.gather(*(run_agent(hub, name) for name in peers))

# Variant 2: dynamic lead — team size decided by the MODEL at runtime
# lead agent has extra tools: create_subagents(n), get_status(), kill_subagents()
# -> this is a true agent by the workflow/agent test: nobody in code decided
#    how many workers exist or when they stop.
```

Both variants use the same messaging primitive: `send_message`/`wait_for_message` through a shared `Hub`, with each agent's drained inbox appended onto its last tool result at the start of its next turn, rather than the agent polling for new messages.

## Dynamic workflows — the fact-check example, phase by phase

```
EXTRACT   — one agent reads the source document, pulls out discrete factual claims
              │
              ▼  (plain code: fan the claim list out)
VERIFY    — one agent PER claim, run in parallel(), each with a clean context
            (sees only its one claim, not the others) and a structured-output
            schema for its verdict
              │
              ▼  (plain code: filter to only "confirmed" verdicts)
SKEPTIC   — a second pass, re-examining only the claims VERIFY marked confirmed —
            routed here by code, not by a model deciding to double-check
              │
              ▼
REPORT    — one agent compiles everything into a final table
```

The load-bearing detail: the routing between SKEPTIC and REPORT, and the filtering between VERIFY and SKEPTIC, are **plain code**, not a model decision — the workflow script holds the plan, and each spawned agent only ever sees the narrow slice of the problem it was actually invoked for. This is the shape to copy if you're building something with a similar "broad gather → narrow re-check on a subset → synthesize" structure.
