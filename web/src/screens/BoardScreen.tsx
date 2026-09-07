import { api } from "../api";
import type { Board, Task } from "../api-types";
import { useAsync } from "../useAsync";
import { DEMANDS_ATTENTION, Pill, SETTLED, STATE_LABEL, Tag, ago, roomName } from "../ui";

/** The board, as a kanban: one column per task state.
 *
 *  It was a stack of `<details>` — six collapsible sections, then a message
 *  table, on one scroll. Everything was one keystroke away and nothing was
 *  visible, which is the shape of screen you read by remembering where you
 *  put things.
 *
 *  Three rules do the work here, and each is answering "rối mắt" rather than
 *  taste:
 *
 *  **One question per zone.** Failures that need a person, then the work,
 *  then nothing else — the message feed moved to its own screen, grouped by
 *  room, because a flat feed of four channels reads as one argument nobody
 *  is having.
 *
 *  **Columns are not equal.** Only the two that carry an obligation open at
 *  full width. Six equal columns make the reader find the two that matter
 *  every time they look.
 *
 *  **A card says four things.** Type, age, one line of summary, cost.
 *  Everything else is behind the card, which is a button onto that task's
 *  own path. Truncate-and-expand, per the skill's Content rule; the
 *  expansion is a screen, not a taller card. */
export function BoardScreen({
  onOpenFlow,
}: {
  onOpenFlow: (provider: string, id: string) => void;
}) {
  const board = useAsync(() => api.board(), []);
  const spend = useAsync(() => api.spend(), []);
  // The operator's own labels, so a card says which room in words they chose
  // rather than in nineteen digits.
  const rooms = useAsync(() => api.conversations(), []);
  const names = new Map(
    (rooms.value ?? []).map((r) => [r.id, r.name] as const),
  );

  if (board.error) {
    return <div className="card error">Could not load the board: {board.error}</div>;
  }
  if (!board.value) return <p className="empty">Loading…</p>;
  const it: Board = board.value;

  const newest = new Map<number, string | null>();
  for (const m of it.messages) {
    // A task's own `created_at` is when it opened, not when the room last
    // said something about it — and the second is the one that tells you
    // whether it has gone cold.
    for (const [, tasks] of Object.entries(it.tasks_by_state)) {
      for (const t of tasks) {
        if (t.conversation === m.conversation && !newest.has(t.id)) {
          newest.set(t.id, m.created_at);
        }
      }
    }
  }

  return (
    <>
      <div className="row wrap" style={{ justifyContent: "space-between" }}>
        <div className="row wrap">
          <Pill
            tone={it.status === "connected" ? "good" : "warn"}
            label={it.status}
          />
          <span className="faint mono">
            {it.counts.messages} messages · {it.counts.untriaged} untriaged · last{" "}
            {ago(it.counts.last_message_at)}
          </span>
          {spend.value && (
            <span
              className="faint mono"
              // Its own separator: without one this ran straight on from the
              // line before it — "last 7m ago 57752 tok today".
              style={{ borderLeft: "1px solid var(--line)", paddingLeft: 8 }}
              title={Object.entries(spend.value.by_agent)
                .map(([a, n]) => `${a} ${n}`)
                .join(" · ")}
            >
              {spend.value.total} tok today
            </span>
          )}
        </div>
        <button
          onClick={() => {
            board.reload();
            spend.reload();
          }}
        >
          Refresh
        </button>
      </div>

      {it.failed.length > 0 && (
        <section className="banner">
          <Pill tone="bad" label={`${it.failed.length} unsent`} />
          <div style={{ minWidth: 0 }}>
            {/* First and loudest: it is the only thing on this screen that
                needs a person, and the text is here to be copied. */}
            <h2>Could not be sent</h2>
            {it.failed.map((row) => (
              <div key={row.id} style={{ marginTop: 8 }}>
                <span className="faint mono">
                  task {row.task_id ?? "—"} · {row.kind} · {row.attempts} attempts
                </span>
                <pre>{row.text}</pre>
                {row.last_error && <p className="mono error">{row.last_error}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="board">
        {Object.entries(it.tasks_by_state).map(([state, tasks]) => (
          <Column
            key={state}
            state={state}
            tasks={tasks}
            lastSpoke={newest}
            onOpen={onOpenFlow}
            messages={it.messages}
            names={names}
          />
        ))}
      </div>
    </>
  );
}

function Column({
  state,
  tasks,
  lastSpoke,
  messages,
  names,
  onOpen,
}: {
  state: string;
  tasks: Task[];
  lastSpoke: Map<number, string | null>;
  messages: Board["messages"];
  names: Map<string, string | null>;
  onOpen: (provider: string, id: string) => void;
}) {
  const quiet = tasks.length === 0;
  const edge = DEMANDS_ATTENTION.has(state)
    ? " attention"
    : SETTLED.has(state)
      ? " settled"
      : "";
  return (
    <section className={`column${quiet ? " quiet" : ""}${edge}`}>
      <header>
        <h2 title={state}>{STATE_LABEL[state] ?? state}</h2>
        <span className="count">{tasks.length}</span>
      </header>
      <div className="stack">
        {tasks.length === 0 ? (
          // An empty column still says so: "nothing needs you" is the answer
          // somebody came to this screen for.
          <p className="faint mono">nothing</p>
        ) : (
          tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              spokeAt={lastSpoke.get(task.id) ?? task.created_at}
              onOpen={onOpen}
              messages={messages}
              names={names}
            />
          ))
        )}
      </div>
    </section>
  );
}

function TaskCard({
  task,
  spokeAt,
  messages,
  names,
  onOpen,
}: {
  task: Task;
  spokeAt: string | null;
  messages: Board["messages"];
  names: Map<string, string | null>;
  onOpen: (provider: string, id: string) => void;
}) {
  const from = messages.find((m) => m.conversation === task.conversation);
  const summary =
    (task.params["summary"] as string) ??
    (task.params["question"] as string) ??
    (task.params["reason"] as string) ??
    from?.text ??
    "";

  return (
    <button
      className="card-task"
      onClick={() =>
        from
          ? onOpen(from.provider, from.provider_message_id)
          : undefined
      }
      disabled={!from}
      title={
        from
          ? "Open the path this task came from"
          : "No captured message for this task"
      }
    >
      <div className="row wrap">
        <Tag type={task.type} />
        <span className="faint mono">#{task.id}</span>
        <span className="count">{ago(spokeAt)}</span>
      </div>
      {summary && <p className="summary">{summary}</p>}
      <div className="row wrap">
        <Pill
          tone={task.confidence >= 0.7 ? undefined : "warn"}
          label={`${task.confidence.toFixed(2)}`}
          title="how sure the classifier was"
        />
        <span className="faint mono" title={task.conversation}>
          {roomName(task.conversation, names.get(task.conversation))}
        </span>
      </div>
    </button>
  );
}
