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
  /** This message opened a task. Marked on the Rooms row with a task glyph. */
  is_task: boolean;
  /** The task this message belongs to, or null. A board card finds its own
   *  message by this, never by the room. */
  task_id: number | null;
  /** An agent wrote a memory while processing this message. Marked with an enrichment glyph. */
  is_enrichment: boolean;
  model_call: ModelCallSummary | null;
}

export interface ModelCallSummary {
  agent: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface TaskOpening {
  provider: string;
  provider_message_id: string;
  text: string;
}

export interface Task {
  id: number;
  conversation: string;
  type: string;
  state: string;
  confidence: number;
  params: Record<string, unknown>;
  created_at: string | null;
  /** Last activity timestamp. The Monitor screen reads this for
   * "last spoke at"; the BoardScreen reads it to sort cards
   * newest-first within a column. The store populates it from the
   * latest model_call or tool_call on the same task. */
  last_activity_at: string | null;
  /** Number of agent attempts that ran while working on this task.
   * `0` for tasks that never had a model call (an empty plan); the
   * Monitor screen renders this as a small badge so a stuck task is
   * distinguishable from a finished one at a glance. */
  attempts: number;
  /** The message that opened this task, from the server — never looked up
   *  among the board's loaded messages, which are only the newest. `null`
   *  when no message is linked to the task. */
  opening: TaskOpening | null;
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
  /** Database id. Used by the Flow screen to order this turn against
   * the outbound rows that may have followed it (ticket 12). */
  id: number;
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
  /** When the call was made. Wire-side ISO 8601. */
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
  /** `config.yaml`'s `triage.confidence_threshold`. `null` when unset, which
   *  is not the same as 0.7 — the page must not invent the line it draws. */
  confidence_threshold: number | null;
  counts: Counts;
  /** Channels at `MEMORY_PER_CHANNEL` (ticket 12, D18) — the ceiling already
   *  refuses the write and evicts nothing; this is the operator's own view
   *  of the same condition. */
  full_memory_channels: string[];
  failed: Outbound[];
  tasks_by_state: Record<string, Task[]>;
  messages: Message[];
}

/** Today's tokens. An agent that spent nothing is absent rather than zero —
 *  which agents exist is `config.yaml`'s business, so there is no list to
 *  enumerate against. */
export interface Spend {
  total: number;
  by_agent: Record<string, number>;
}

/** One task's calls, both kinds, and what they cost. Two lists rather than
 *  one merged one: they are different shapes, and a reader can interleave on
 *  `created_at` without a tag invented for it. */
export interface TaskCalls {
  model_calls: ModelCall[];
  tool_calls: ToolCall[];
  spent: number;
}

/** Whether node 0's own budget-based truncation has stopped trying for one
 *  task (ticket 08 of `what-the-room-already-knows`). A task whose
 *  `ineffective_count` is above zero has a transcript truncation cannot
 *  bring under its configured budget; `on_cooldown` once that has happened
 *  twice, at which point node 0 stops re-checking on every pass. Zero and
 *  `false` for a task nothing has ever recorded against, and for every
 *  install with no budget configured at all. */
export interface TaskCompaction {
  ineffective_count: number;
  on_cooldown: boolean;
}

/** A room in the left-hand list. `name` is the operator's own label and
 *  `null` when they have not given one — which the list renders as the
 *  channel, not as an empty string. */
export interface Room {
  id: string;
  name: string | null;
  channel_id: string;
  messages: number;
  last_at: string | null;
}

/** Something an agent chose to write down about a room. `deleted_at` is set
 *  rather than the row removed, so the operator can see what was forgotten
 *  and who forgot it.
 *
 *  `kind` is one of thirteen — `fact` / `constraint` / `finding` /
 *  `decision` / `voice`, and the operator's `runbook` / `project` /
 *  `service` / `route` / `dependency` / `person` / `environment`, plus
 *  `summary` — and who reads a row follows from it. The page never spells
 *  that list out: the dropdown and every field in the form come from
 *  `/api/memory-kinds`, so a new kind needs no frontend change. `status` is `active` or `superseded`; a
 *  superseded row's `superseded_by` names the row that replaced it, and the
 *  row itself survives so the board can show what it used to say and when
 *  (`updated_at`).
 *
 *  `origin` is `model` or `admin` (the operator, through this page). A
 *  structured kind carries its payload in `data` and its natural key in
 *  `key`; both are `null` for prose. */
export interface Memory {
  id: string;
  channel_id: string;
  agent: string;
  text: string;
  kind: string;
  status: string;
  superseded_by: string | null;
  task_id: number | null;
  source_message_id: string | null;
  origin: string;
  key: string | null;
  data: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  deleted_by: string | null;
}

/** One kind the operator may write, and what its form asks for — served by
 *  `/api/memory-kinds`, read off the same schemas the store checks. `prose`
 *  kinds are a text area alone; `names_key` is the runbook, whose key is a
 *  name the operator gives rather than one read off its data. */
export interface MemoryKindForm {
  kind: string;
  prose: boolean;
  names_key: boolean;
  fields: MemoryField[];
}

/** One form field. `name` is dotted for a nested object (`prod.cluster`).
 *  `type` is `text`, `number`, `list` (comma-separated), `choice` (one of
 *  `choices`) or `json` (a list of objects, typed by hand). */
export interface MemoryField {
  name: string;
  type: string;
  required: boolean;
  choices: string[];
}

/** A memory an agent proposed, waiting for the operator's mark — or already
 *  marked (board `what-the-room-already-knows`, ticket 12). `status` is
 *  `"pending"`, `"accepted"` or `"rejected"`; `memory_id` is set only once
 *  accepted, and only if the write actually landed.
 */
export interface MemoryCandidate {
  id: string;
  channel_id: string;
  agent: string;
  text: string;
  kind: string;
  task_id: number | null;
  source_message_id: string | null;
  status: string;
  proposed_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  memory_id: string | null;
}

/** A line on the Monitor screen's live feed.
 *
 *  The feed is a single ordered list of "something happened". Today
 *  that is a model call or a tool call; SSE (ticket 05) will append
 *  more kinds. The discriminator is `type`, and the screen reads
 *  `kind` off it. The union is open: a future `type: "task_opened"`
 *  arrives, the screen renders it, the type does not have to be
 *  closed here.
 */
export interface MonitorEvent {
  /** Monotonic id within the feed — used as the React `key` so an
   * appended event never re-uses a row's id and the React reconciler
   * never re-mounts a row it already mounted. */
  id: number;
  /** One of `"model_call"` or `"tool_call"`. New kinds (e.g. a future
   * `"task_state_changed"`) extend the union without changing the
   * wire shape. */
  type: "model_call" | "tool_call";
  /** ISO 8601 timestamp. The screen sorts on it. */
  occurred_at: string;
  /** What happened, in the operator's vocabulary. A model call is
   *  `agent`; a tool call is `tool`. The screen renders the same
   *  shape for both: an actor name, then a one-line description,
   *  then a latency. The fields differ, the rendering does not. */
  agent: string;
  tool: string | null;
  latency_ms: number | null;
  /** Did the call succeed, fail, or hang? Same enum the Flow
   *  screen uses — the audit's "same word everywhere" rule. */
  state: "done" | "failed" | "retrying" | "ok";
  /** `provider:provider_message_id` of the message that opened
   * the task this event belongs to. The Monitor screen reads
   * this to drill into the flow when the operator clicks a
   * row; without it, the click does nothing. `null` for snapshot
   * events whose lookup is not yet wired on that path — the row
   * stays a passive span in that case. */
  message_id: string | null;
}

/** A running task — not finished, not handed off. The Monitor
 *  screen's right-hand column shows one card per running task;
 *  the operator's two questions at a glance are "is anything
 *  stuck" and "what is it doing". */
export interface RunningTask {
  id: number;
  type: string;
  state: string;
  /** Display name of the room the task belongs to, not the
   *  channel id. The store resolves the conversation. */
  room: string;
  /** The message that opened this task — `provider:message_id`.
   *  The Monitor screen reads this to drill into the flow when
   *  the operator clicks a card; without it, the link from the
   *  Monitor screen back to the originating message would need
   *  a second round trip. */
  message_id: string | null;
  /** Most recent activity timestamp — "5s ago", "2m ago". */
  last_activity_at: string | null;
  /** What the agent did most recently. */
  last_tool: string | null;
  attempts: number;
}

/** One snapshot of the Monitor screen. The page asks for this on
 *  mount, then subscribes to SSE for live updates (ticket 05). */
export interface MonitorSnapshot {
  status: "connected" | "disconnected";
  events: MonitorEvent[];
  running_tasks: RunningTask[];
  counts: {
    messages: number;
    untriaged: number;
    last_message_at: string | null;
    spend_today: number;
  };
}
