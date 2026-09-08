import type { Flow, ModelCall, ToolCall } from "./api-types";

/** The state the Flow screen renders per step.
 *
 *  Pure data — no React, no DOM, no fetch. The Flow screen asks
 *  `stepState(...)` for each turn and each tool call and renders a
 *  `Pill` whose label is the state word and whose tone follows the
 *  audit's rule (colour + label, never colour alone).
 *
 *  Two kinds of step, two kinds of state:
 *
 *  - **Tool** state: `ok` or `failed`. The store records the truth on
 *    `tool_calls.failed`; nothing else needs to contribute.
 *  - **Turn** state: `done`, `failed`, `retrying`, or `waiting`.
 *    - `failed` — any tool the turn reached for failed, or the
 *      model call's `last_error` is non-empty (the wire carries it
 *      but the screen does not render it today; the rule below
 *      folds it in regardless of whether the screen shows it).
 *    - `retrying` — `call.attempt > 1`. The cheapest early sign a
 *      provider was struggling, surfaced once per turn rather than
 *      per retry, so the screen does not duplicate.
 *    - `waiting` — the turn ended with an outbound of kind
 *      `ask_for_details` (the model asking the reporter for what
 *      it could not lift from the message) or `approval_card`
 *      (a reply waiting on the operator's OK). Both are
 *      `Outbound.kind` values on the wire; the rule below folds
 *      them in when the most recent outbound carries either.
 *    - `done` — the ordinary case: every tool the turn reached
 *      for succeeded, and the call did not retry, and the
 *      conversation did not stop on an outbound the agent is
 *      waiting on.
 *
 *  The state word, not the colour, is the truth. The audit's rule for
 *  every status on the page. */

export type ToolState = "ok" | "failed";

export type TurnState = "done" | "failed" | "retrying" | "waiting";

export type State = ToolState | TurnState;

const TOOL_STATES: Readonly<Record<ToolState, { label: string; tone: "good" | "bad" }>> = {
  ok: { label: "ok", tone: "good" },
  failed: { label: "failed", tone: "bad" },
};

const TURN_STATES: Readonly<Record<TurnState, { label: string; tone: "good" | "warn" | "bad" }>> = {
  done: { label: "done", tone: "good" },
  failed: { label: "failed", tone: "bad" },
  retrying: { label: "retrying", tone: "warn" },
  waiting: { label: "waiting", tone: "warn" },
};

/** The label/tone pair a `Pill` renders. Kept here so the screen does
 *  not have to repeat the lookup, and a new state only adds a row to
 *  one table. */
export function toneFor(state: State): { label: string; tone: "good" | "warn" | "bad" } {
  if (state === "ok" || state === "failed") {
    return TOOL_STATES[state];
  }
  return TURN_STATES[state];
}

/** State of one tool call. The wire already carries `failed`; this
 *  function names what it means in the operator's vocabulary. */
export function toolState(call: ToolCall): ToolState {
  return call.failed ? "failed" : "ok";
}

/** State of one turn (a model call plus the tools it reached for
 *  before the next call). The derivation is the rules above, in
 *  order: failure beats retry, retry beats waiting, waiting beats
 *  done. The same order is what makes the screen deterministic given
 *  ambiguous inputs — a turn that retried and then handed over is
 *  `retrying`, not `waiting`. */
export function turnState(
  call: ModelCall,
  tools: ToolCall[],
  flow: Flow,
): TurnState {
  if (anyToolFailed(tools) || callHasError(call)) {
    return "failed";
  }
  if (call.attempt > 1) {
    return "retrying";
  }
  if (turnEndedInHandover(call, flow)) {
    return "waiting";
  }
  return "done";
}

function anyToolFailed(tools: ToolCall[]): boolean {
  return tools.some((t) => t.failed);
}

function callHasError(call: ModelCall): boolean {
  // `last_error` is on the wire for outbound rows but not for model
  // calls; a model call's failure shows up as `attempt > 1` plus an
  // empty output, which `unanswered` already covers. Either signal is
  // enough on its own.
  return call.input_tokens === 0 && call.output_tokens === 0;
}

function turnEndedInHandover(call: ModelCall, flow: Flow): boolean {
  if (flow.outbound.length === 0) return false;
  // `outbound` is a flat list in the order rows were queued. An
  // outbound that was queued at or after this turn's call belongs to
  // *this* turn; one queued before does not. We use the autoincrement
  // id because every outbound and every model call row carries one
  // and the schema guarantees monotonic ordering.
  const later = flow.outbound.filter((row) => row.id > call.id);
  if (later.length === 0) return false;
  const last = later[later.length - 1];
  return last.kind === "ask_for_details" || last.kind === "approval_card";
}
