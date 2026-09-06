import { api } from "../api";
import type { ModelCall, Task, ToolCall } from "../api-types";
import { useAsync } from "../useAsync";

/** Board ticket 06 — what a task did, and what it cost.
 *
 *  Two questions and no more: "why did it do that", which is why prompts are
 *  shown in full rather than previewed, and "what am I paying for", which is
 *  why tokens are summed rather than merely listed. */
export function TasksScreen({
  onOpenFlow,
}: {
  onOpenFlow: (provider: string, id: string) => void;
}) {
  const board = useAsync(() => api.board(), []);
  const spend = useAsync(() => api.spend(), []);

  if (board.error) {
    return <div className="card error">Could not load the board: {board.error}</div>;
  }
  if (!board.value) return <div className="empty">Loading…</div>;

  const states = Object.keys(board.value.tasks_by_state);
  const messages = board.value.messages;

  return (
    <>
      <div className="card row wrap" style={{ justifyContent: "space-between" }}>
        <div className="row wrap">
          <Pill
            tone={board.value.status === "connected" ? "good" : "warn"}
            label={board.value.status}
          />
          {Object.entries(board.value.counts).map(([what, n]) => (
            <span key={what} className="mono muted">
              {what} <strong>{n}</strong>
            </span>
          ))}
        </div>
        <div className="row wrap">
          {/* `daily_token_budget` ships unset on purpose — a number guessed
              before anyone knows what a normal day costs makes the first busy
              day look like a fault. This is how it stops being a guess. */}
          {spend.value && (
            <span className="mono muted">
              today <strong>{spend.value.total}</strong> tok
              {Object.entries(spend.value.by_agent).map(([agent, n]) => (
                <span key={agent} className="faint">
                  {" "}
                  · {agent} {n}
                </span>
              ))}
            </span>
          )}
          <button
            onClick={() => {
              board.reload();
              spend.reload();
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {board.value.failed.length > 0 && (
        <section className="card">
          {/* First, because it is the only thing here that needs a person —
              the old board put it first for the same reason, and the text is
              here to be copied. */}
          <h2>Could not be sent</h2>
          {board.value.failed.map((row) => (
            <div key={row.id} className="card" style={{ marginTop: 8 }}>
              <div className="row wrap">
                <Pill tone="bad" label="failed" />
                <span className="faint mono">
                  task {row.task_id ?? "—"} · {row.kind} · {row.attempts} attempts
                </span>
              </div>
              <pre>{row.text}</pre>
              {row.last_error && <p className="mono error">{row.last_error}</p>}
            </div>
          ))}
        </section>
      )}

      <section className="card">
        <h2>Tasks</h2>
        {states.map((state) => (
          <details key={state} open={board.value!.tasks_by_state[state].length > 0}>
            <summary>
              {state} · {board.value!.tasks_by_state[state].length}
            </summary>
            {board.value!.tasks_by_state[state].length === 0 ? (
              /* An empty state is information; a missing one is a bug the
                 operator cannot see. */
              <p className="faint mono">nothing here</p>
            ) : (
              board.value!.tasks_by_state[state].map((task) => (
                <TaskCard key={task.id} task={task} />
              ))
            )}
          </details>
        ))}
      </section>

      <section className="card">
        <h2>Recent messages</h2>
        <table>
          <thead>
            <tr>
              <th>when</th>
              <th>who</th>
              <th>said</th>
              <th>cost</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {messages.map((m) => (
              <tr key={`${m.provider}:${m.provider_message_id}`}>
                <td className="mono faint">{shortTime(m.created_at)}</td>
                <td className="mono">{m.author_name}</td>
                <td>{m.text}</td>
                <td className="mono faint">
                  {m.model_call
                    ? `${m.model_call.input_tokens + m.model_call.output_tokens} tok`
                    : "—"}
                </td>
                <td>
                  <button
                    onClick={() => onOpenFlow(m.provider, m.provider_message_id)}
                  >
                    Path
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {messages.length === 0 && (
          <p className="empty">
            Nothing captured yet. The database was rebuilt empty — start the agent
            and mention it in a watched channel.
          </p>
        )}
      </section>
    </>
  );
}

function TaskCard({ task }: { task: Task }) {
  const calls = useAsync(
    () => fetch(`/api/tasks/${task.id}/model-calls`).then((r) => r.json()),
    [task.id],
  );
  const spent = ((calls.value as ModelCall[]) ?? []).reduce(
    (n, c) => n + c.input_tokens + c.output_tokens,
    0,
  );

  return (
    <div className="card" style={{ marginTop: 8 }}>
      <div className="row wrap">
        <strong className="mono">
          {task.type} #{task.id}
        </strong>
        <Pill label={`confidence ${task.confidence.toFixed(2)}`} />
        {spent > 0 && <Pill label={`${spent} tok`} />}
      </div>
      {Object.keys(task.params).length > 0 && (
        <pre>{JSON.stringify(task.params, null, 2)}</pre>
      )}
      <details>
        <summary>What it asked the model ({(calls.value as ModelCall[])?.length ?? 0})</summary>
        {((calls.value as ModelCall[]) ?? []).map((c, i) => (
          <CallCard key={i} call={c} />
        ))}
      </details>
    </div>
  );
}

export function CallCard({ call }: { call: ModelCall }) {
  return (
    <div className="card" style={{ marginTop: 8 }}>
      <div className="row wrap">
        <strong className="mono">{call.agent}</strong>
        <span className="faint mono">{call.model}</span>
        {call.node && <Pill label={`node ${call.node}`} />}
        {call.latency_ms != null && <Pill label={`${call.latency_ms} ms`} />}
        <Pill label={`${call.input_tokens} in / ${call.output_tokens} out`} />
        {/* A retry is the cheapest early sign a provider is struggling, so it
            is visible without expanding anything. */}
        {call.attempt > 1 && <Pill tone="warn" label={`attempt ${call.attempt}`} />}
      </div>
      <details>
        <summary>Prompt</summary>
        <pre>{call.system_prompt}</pre>
        <pre>{call.prompt}</pre>
      </details>
      <details>
        <summary>Answer</summary>
        <pre>{call.output}</pre>
      </details>
    </div>
  );
}

export function ToolCard({ call }: { call: ToolCall }) {
  return (
    <div className="card" style={{ marginTop: 8 }}>
      <div className="row wrap">
        <strong className="mono">{call.tool}</strong>
        <span className="faint mono">{call.agent}</span>
        {/* `failed` is its own word, not a colour: a tool failure is turned
            into an ordinary-looking message for the model, so the flag is the
            only thing that distinguishes it from an answer. */}
        <Pill tone={call.failed ? "bad" : "good"} label={call.failed ? "failed" : "ok"} />
        {call.latency_ms != null && <Pill label={`${call.latency_ms} ms`} />}
      </div>
      <details>
        <summary>Arguments and result</summary>
        <pre>{call.arguments}</pre>
        <pre>{call.result}</pre>
      </details>
    </div>
  );
}

export function Pill({
  label,
  tone,
}: {
  label: string;
  tone?: "good" | "warn" | "bad";
}) {
  return <span className={tone ? `pill ${tone}` : "pill"}>{label}</span>;
}

export function shortTime(iso: string | null): string {
  if (!iso) return "—";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleTimeString();
}
