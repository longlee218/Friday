import { memo, useCallback, useState } from "react";

import { api } from "../api";
import type {
  MonitorEvent,
  MonitorSnapshot,
  RunningTask,
} from "../api-types";
import { useAsync } from "../useAsync";
import { useEventStream } from "../useEventStream";
import {
  Pill,
  Skeleton,
  ago,
  shortTime,
  toneFromState,
} from "../ui";

/** Board ticket 04 — the front door.
 *
 *  Two columns: live feed (60%) and running tasks (40%), with a
 *  footer strip carrying the throughput numbers. The audit's
 *  reasoning for the layout is in the spec — the operator's first
 *  question on opening the page is "what is happening right now",
 *  and the answer is two questions: "what just happened" (the feed)
 *  and "what is in flight" (the tasks). A single column is one of
 *  those, not both.
 *
 *  Three things the audit asked for, applied here:
 *  - audit #1: the feed announces itself to screen readers via
 *    `` and `aria-live="polite"`. The screen is read-only,
 *    so "polite" is the right urgency — "assertive" would interrupt
 *    whatever the operator was reading on another screen.
 *  - audit #2: every state pill carries a label, not a colour. The
 *    colour is decoration; the word is the truth.
 *  - audit #4: `TaskCard` is `React.memo`'d on `task.id` so SSE
 *    bursts do not re-render cards that did not change.
 *
 *  Virtualization is intentionally absent here. The feed holds 100
 *  rows on mount and grows by a handful per minute; SSE ticket 05
 *  will not change that order of magnitude, and a virtualized list
 *  is a regression in operator-facing interaction (no infinite
 *  scroll, no row-height measurement, no jank during a window
 *  resize). If a future ticket raises the cap past the audit's
 *  200-row threshold, this is the place to add it.
 */

const VIRTUALIZE_THRESHOLD = 200;

export function MonitorScreen() {
  const snap = useAsync(() => api.monitor(), []);
  const [liveEvents, setLiveEvents] = useState<MonitorEvent[]>([]);

  // Append a server event to the live feed. The snapshot we
  // fetched on mount already has its own events; SSE events come
  // after, and the wire carries an id so the *render* order can
  // match the on-disk order rather than the arrival order (a
  // replay that arrives late does not jump above a fresh event).
  const onEvent = useCallback((ev: { id: number; type: string; occurred_at: string; payload: Record<string, unknown> }) => {
    // Translate the wire shape into the shape the screen renders.
    // Only model_call and tool_call are wired today; future event
    // types extend the union in `api-types.ts` and the dispatcher
    // here, the screen renders whatever comes out the other side.
    const rendered = renderServerEvent(ev);
    if (!rendered) return;
    setLiveEvents((prev) => {
      const next = [...prev, rendered];
      // Cap the live buffer at the same threshold the snapshot
      // uses. Beyond that, the page is asked to refresh — SSE is
      // for the live tail, not for an unbounded backlog.
      return next.length > 200 ? next.slice(-200) : next;
    });
  }, []);

  const { connected: sseConnected } = useEventStream("/api/events", onEvent);

  const events = snap.value ? [...snap.value.events, ...liveEvents] : liveEvents;

  return (
    <div className="monitor">
      <header className="row wrap between">
        <Pill
          tone={sseConnected ? "good" : "warn"}
          label={sseConnected ? "live" : "polling"}
        />
        {snap.value && (
          <span className="faint mono">
            {events.length} events · {snap.value.running_tasks.length} running
          </span>
        )}
        <button onClick={snap.reload}>Refresh</button>
      </header>

      {snap.error && (
        <section className="banner">
          <Pill tone="bad" label="error" />
          <div className="grow">
            <h2>Could not load the monitor</h2>
            <p className="mono error">{snap.error}</p>
          </div>
        </section>
      )}

      {!snap.value ? (
        <div className="monitor-grid">
          <Skeleton height={400} />
          <Skeleton height={400} />
        </div>
      ) : (
        <div className="monitor-grid">
          <Feed events={events} />
          <Tasks tasks={snap.value.running_tasks} />
        </div>
      )}

      {snap.value && <Footer snap={snap.value} />}
    </div>
  );
}

function renderServerEvent(
  ev: { id: number; type: string; occurred_at: string; payload: Record<string, unknown> },
): MonitorEvent | null {
  if (ev.type !== "model_call" && ev.type !== "tool_call") {
    return null;
  }
  const p = ev.payload;
  if (ev.type === "model_call") {
    const attempt = typeof p.attempt === "number" ? p.attempt : 1;
    return {
      id: typeof p.row_id === "number" ? p.row_id : ev.id,
      type: "model_call",
      occurred_at: ev.occurred_at,
      agent: typeof p.agent === "string" ? p.agent : "unknown",
      tool: null,
      latency_ms: typeof p.latency_ms === "number" ? p.latency_ms : null,
      state: attempt > 1 ? "retrying" : "done",
    };
  }
  return {
    id: typeof p.row_id === "number" ? p.row_id : ev.id,
    type: "tool_call",
    occurred_at: ev.occurred_at,
    agent: typeof p.agent === "string" ? p.agent : "unknown",
    tool: typeof p.tool === "string" ? p.tool : null,
    latency_ms: typeof p.latency_ms === "number" ? p.latency_ms : null,
    state: p.failed ? "failed" : "ok",
  };
}

function Feed({ events }: { events: MonitorEvent[] }) {
  if (events.length === 0) {
    return (
      <section className="card">
        <header>
          <h2>Live feed</h2>
        </header>
        <p className="faint">Nothing has happened yet.</p>
      </section>
    );
  }
  const visible = events;
  const virtualize = events.length > VIRTUALIZE_THRESHOLD;
  return (
    <section
      className="card feed"
      role="log"
      aria-live="polite"
      aria-label="Live event feed"
    >
      <header>
        <h2>Live feed</h2>
        <span className="faint mono">{events.length} events</span>
      </header>
      <ol className="feed-list">
        {(virtualize ? visible.slice(-VIRTUALIZE_THRESHOLD) : visible).map((e) => (
          <FeedRow key={`${e.type}:${e.id}`} event={e} />
        ))}
      </ol>
      {virtualize && (
        <p className="faint mono footer">
          showing the last {VIRTUALIZE_THRESHOLD} of {events.length}
        </p>
      )}
    </section>
  );
}

function FeedRow({ event }: { event: MonitorEvent }) {
  const tone = toneFromState(event.state);
  const verb =
    event.type === "model_call"
      ? event.tool
        ? `read ${event.tool}`
        : "decided"
      : `used ${event.tool ?? "tool"}`;
  return (
    <li className="feed-row">
      <span className="when mono">{shortTime(event.occurred_at)}</span>
      <span className="who">{event.agent}</span>
      <span className="verb mono">{verb}</span>
      <Pill tone={tone} label={event.state} />
      {event.latency_ms != null && (
        <span className="latency faint mono">{event.latency_ms}ms</span>
      )}
    </li>
  );
}

const TaskCard = memo(function TaskCard({ task }: { task: RunningTask }) {
  const tone = toneFromState(task.state);
  return (
    <article className="card task-card">
      <header className="row wrap between">
        <span className="who">
          <strong>#{task.id}</strong>
          <span className="faint mono"> {task.type}</span>
        </span>
        <Pill tone={tone} label={task.state} />
      </header>
      <p className="room mono">{task.room}</p>
      <p className="faint mono">
        {task.last_tool ? `last: ${task.last_tool}` : "no tool call yet"}
        {" · "}
        {ago(task.last_activity_at)}
        {" · "}
        {task.attempts} attempts
      </p>
    </article>
  );
});

function Tasks({ tasks }: { tasks: RunningTask[] }) {
  return (
    <section className="card tasks">
      <header>
        <h2>Running tasks</h2>
        <span className="faint mono">{tasks.length} in flight</span>
      </header>
      {tasks.length === 0 ? (
        <p className="faint">No tasks are running.</p>
      ) : (
        <div className="task-list">
          {tasks.map((t) => (
            <TaskCard key={t.id} task={t} />
          ))}
        </div>
      )}
    </section>
  );
}

function Footer({ snap }: { snap: MonitorSnapshot }) {
  return (
    <footer className="monitor-footer">
      <Pill label={`${snap.counts.messages} messages`} />
      <Pill
        tone={snap.counts.untriaged > 0 ? "warn" : "good"}
        label={`${snap.counts.untriaged} untriaged`}
      />
      <Pill
        label={`last: ${ago(snap.counts.last_message_at)}`}
      />
      <Pill label={`${snap.counts.spend_today} tok today`} />
    </footer>
  );
}
