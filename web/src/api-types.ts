// What the API actually sends.
//
// Hand-written, not generated — and the reason is worth knowing before
// somebody reaches for a codegen tool. FastAPI's `/openapi.json` describes
// every response here as `{"type": "object", "additionalProperties": true}`,
// because the routes are annotated `-> dict` and build their bodies by hand.
// A client generated from that schema would catch a renamed *route* and would
// not catch a renamed *field*, which is the failure that actually costs
// something: a page rendering `undefined` in silence.
//
// So the guard is on the Python side instead. `tests/test_web_contract.py`
// calls the real converters, reads the keys they emit, and fails when they
// stop matching the fields declared here. Rename a key in `friday/ops/api.py`
// and `uv run pytest -q` goes red naming this file.

export interface Message {
  provider: string;
  provider_message_id: string;
  conversation: string;
  author_name: string;
  text: string;
  created_at: string | null;
  mention_type: string | null;
  is_own: boolean;
  model_call: ModelCallSummary | null;
}

export interface ModelCallSummary {
  agent: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface Task {
  id: number;
  conversation: string;
  type: string;
  state: string;
  confidence: number;
  params: Record<string, unknown>;
  created_at: string | null;
}

export interface Outbound {
  id: number;
  task_id: number | null;
  conversation: string;
  kind: string;
  sender: string;
  text: string;
  reply_to: string | null;
  state: string;
  attempts: number;
  last_error: string | null;
}

export interface ModelCall {
  agent: string;
  model: string;
  system_prompt: string;
  prompt: string;
  output: string;
  input_tokens: number;
  output_tokens: number;
  message_id: string | null;
  task_id: number | null;
  node: string | null;
  latency_ms: number | null;
  attempt: number;
  created_at: string;
}

export interface ToolCall {
  agent: string;
  tool: string;
  arguments: string;
  result: string;
  failed: boolean;
  latency_ms: number | null;
  message_id: string | null;
  task_id: number | null;
  node: string | null;
  created_at: string;
}

/** The triage verdict as `mark_triaged` stored it. `null` means nothing has
 *  looked at this message yet — a state, not an absence. */
export interface Decision {
  type: string | null;
  confidence: number | null;
  params: Record<string, unknown>;
}

/** Everything that followed from one message. The spine is a message and not
 *  a task, so a `skip` and a message the prefilter held are both complete
 *  paths rather than empty ones. */
export interface Flow {
  message: Message;
  turn: Message[];
  decision: Decision | null;
  triaged_at: string | null;
  task: Task | null;
  model_calls: ModelCall[];
  tool_calls: ToolCall[];
  outbound: Outbound[];
}

/** `db.counts()` is deliberately mixed, not a flat tally: two of its entries
 *  are themselves breakdowns by state, and one is a timestamp. Typing it
 *  `Record<string, number>` is what crashed the first build of this page —
 *  React will not render an object as a child. */
export interface Counts {
  messages: number;
  untriaged: number;
  last_message_at: string | null;
  tasks: Record<string, number>;
  outbound: Record<string, number>;
}

export interface Board {
  status: string;
  counts: Counts;
  failed: Outbound[];
  tasks_by_state: Record<string, Task[]>;
  messages: Message[];
}

/** A channel's three context layers, plus what an agent is actually told.
 *  `prompt` is the rendering on disk; `live` is the one the running agents
 *  hold. They differ exactly between saving and reloading. */
export interface ChannelContext {
  channel_id: string;
  exists: boolean;
  base: Record<string, string>;
  derived: Record<string, string>;
  overrides: Record<string, string>;
  prompt: string;
  live: string | null;
  /** Keys also present in `base` or `derived`. The model is shown both
   *  sections and reconciles them itself — there is no merge that picks a
   *  winner, which is why this is worth saying out loud. */
  also_in: string[];
}

/** Today's tokens. An agent that spent nothing is absent rather than zero —
 *  which agents exist is `config.yaml`'s business, so there is no list to
 *  enumerate against. */
export interface Spend {
  total: number;
  by_agent: Record<string, number>;
}
