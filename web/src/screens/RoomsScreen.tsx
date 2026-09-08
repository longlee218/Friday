import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { Memory, Message, Room } from "../api-types";
import { useAsync } from "../useAsync";
import { ContextPanel } from "./ContextPanel";
import { Pill, Skeleton, ago, roomName, shortTime } from "../ui";

/** Rooms: the list on the left, one room's messages on the right.
 *
 *  Three things moved here, and each one moved because it belongs to a room
 *  rather than to the application:
 *
 *  **Context** was its own tab. It is a room's own knowledge — the layer the
 *  machine never writes, keyed by channel — so reaching it meant leaving the
 *  room to go and find it by id in a different list. It opens over the room
 *  now, in a dialog.
 *
 *  **Memory** had a route and no reader at all. What an agent chose to write
 *  down about a room is the other half of what that room is told, and the
 *  operator could see one half and not the other.
 *
 *  **"Path"** was a tab nobody could explain, which is fair: it asked you to
 *  know a message id before it would show you anything. It is what a
 *  timestamp does now — click when a message was sent and you get what
 *  happened because of it. The screen it opens is unchanged; only the way in
 *  is. */
export function RoomsScreen({
  onOpenFlow,
}: {
  onOpenFlow: (provider: string, id: string) => void;
}) {
  const rooms = useAsync(() => api.conversations(), []);
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    if (picked === null && rooms.value?.length) setPicked(rooms.value[0].id);
  }, [rooms.value, picked]);

  if (rooms.error) {
    return <div className="card error">Could not load rooms: {rooms.error}</div>;
  }
  if (!rooms.value) {
    return (
      <nav className="rooms">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} height={44} />
        ))}
      </nav>
    );
  }
  if (rooms.value.length === 0) {
    return (
      <p className="empty">
        No room has said anything yet. Start the agent and mention it in a
        watched channel.
      </p>
    );
  }

  const room = rooms.value.find((r) => r.id === picked) ?? rooms.value[0];

  return (
    <div className="split">
      <nav className="rooms" aria-label="Rooms">
        {rooms.value.map((r) => (
          <button
            key={r.id}
            className="room"
            aria-current={r.id === room.id}
            onClick={() => setPicked(r.id)}
          >
            <span className="label" title={r.id}>
              {roomName(r.id, r.name)}
            </span>
            <span className="count">{ago(r.last_at)}</span>
            <span className="meta">
              {r.messages} {r.messages === 1 ? "message" : "messages"}
              {r.name === null && " · unnamed"}
            </span>
          </button>
        ))}
      </nav>

      <RoomDetail
        key={room.id}
        room={room}
        onRenamed={rooms.reload}
        onOpenFlow={onOpenFlow}
      />
    </div>
  );
}

function RoomDetail({
  room,
  onRenamed,
  onOpenFlow,
}: {
  room: Room;
  onRenamed: () => void;
  onOpenFlow: (provider: string, id: string) => void;
}) {
  const feed = useAsync(() => api.messagesIn(200), [room.id]);
  const [showing, setShowing] = useState<"context" | "memory" | null>(null);

  const messages = (feed.value ?? [])
    .filter((m) => m.conversation === room.id)
    .slice()
    .reverse();

  return (
    <section className="room-detail">
      <header className="card row wrap between">
        <Rename room={room} onRenamed={onRenamed} />
        <div className="row">
          <button onClick={() => setShowing("context")}>What it is told</button>
          <button onClick={() => setShowing("memory")}>What it remembers</button>
        </div>
      </header>

      {showing === "context" && (
        <Dialog title={`Context · ${roomName(room.id, room.name)}`} onClose={() => setShowing(null)}>
          <ContextPanel channelId={room.channel_id} />
        </Dialog>
      )}
      {showing === "memory" && (
        <Dialog title={`Memory · ${roomName(room.id, room.name)}`} onClose={() => setShowing(null)}>
          <MemoryPanel channelId={room.channel_id} />
        </Dialog>
      )}

      <div className="thread">
        {feed.error && <p className="msg error">{feed.error}</p>}
        {!feed.value &&
          Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} height={48} />
          ))}
        {messages.map((m, i) => (
          <div key={`${m.provider}:${m.provider_message_id}`} className="contents">
            {/* A thread spanning days shows clock times that run backwards —
                10:41 PM then 11:33 AM — with nothing saying a night passed. */}
            {(i === 0 || day(messages[i - 1]) !== day(m)) && (
              <div className="dayline">
                <span>{day(m)}</span>
              </div>
            )}
            <div
              className={
                // A change of *side* starts a new block even when the name is
                // identical, and here it usually is: replies go out as the
                // watched account, so what this system sent and what the
                // operator typed share an `author_name`.
                i > 0 &&
                messages[i - 1].author_name === m.author_name &&
                messages[i - 1].is_own === m.is_own
                  ? "msg same-author"
                  : "msg"
              }
            >
              <span className="who" title={m.author_name}>
                {m.author_name}
                {m.is_own && (
                  <Pill
                    label="Agent"
                    title="Sent by the watched account on the operator's behalf"
                  />
                )}
                {/* Two glyphs the operator scans the room by. The colour
                    is decoration — the labels are the truth, and they are
                    the only thing the screen reader reads. Glyph + label
                    is the rule everywhere else on this page. They live
                    in the author's column so they line up with the
                    name and stay a glance away from the text. */}
                {m.is_task && (
                  <span
                    className="msg-marker"
                    aria-label="Opened a task"
                    title="This message opened a task"
                  >
                    ✓ task
                  </span>
                )}
                {m.is_enrichment && (
                  <span
                    className="msg-marker"
                    aria-label="Source of a memory"
                    title="An agent wrote a memory while processing this message"
                  >
                    ✎ memory
                  </span>
                )}
              </span>
              <span className="said">{m.text}</span>
              <span className="when">
                <button
                  className="time-link"
                  onClick={() => onOpenFlow(m.provider, m.provider_message_id)}
                  title="What happened because of this message"
                >
                  {shortTime(m.created_at)}
                </button>
              </span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Rename({ room, onRenamed }: { room: Room; onRenamed: () => void }) {
  const [editing, setEditing] = useState(false);
  const [typed, setTyped] = useState(room.name ?? "");
  const [problem, setProblem] = useState<string | null>(null);

  async function save() {
    setProblem(null);
    try {
      await api.rename(room.id, typed);
      setEditing(false);
      onRenamed();
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  if (!editing) {
    return (
      <div className="row wrap">
        <h1>{roomName(room.id, room.name)}</h1>
        <span className="faint mono" title={room.id}>
          {room.channel_id}
        </span>
        <button onClick={() => setEditing(true)}>Rename</button>
        {problem && <span className="mono error">{problem}</span>}
      </div>
    );
  }

  return (
    <div className="row wrap">
      <input
        type="text"
        aria-label="Room name"
        placeholder="what you call this room"
        value={typed}
        autoFocus
        onChange={(e) => setTyped(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
          if (e.key === "Escape") setEditing(false);
        }}
        className="rename-input"
      />
      <button className="primary" onClick={save}>
        Save
      </button>
      <button onClick={() => setEditing(false)}>Cancel</button>
      {/* Emptying the field is how a name is taken back — the route turns an
          empty string into no name rather than storing one that renders as
          nothing. Said out loud because nobody guesses it. */}
      <span className="faint">empty to remove the name</span>
    </div>
  );
}

function MemoryPanel({ channelId }: { channelId: string }) {
  const held = useAsync(() => api.memories(channelId), [channelId]);

  if (held.error) return <p className="mono error">{held.error}</p>;
  if (!held.value) {
    return Array.from({ length: 3 }).map((_, i) => (
      <Skeleton key={i} height={64} />
    ));
  }
  if (held.value.length === 0) {
    return (
      <p className="empty">
        Nothing written down about this room yet. The responder writes these
        itself, through its own tools — nobody types them here.
      </p>
    );
  }

  return (
    <>
      <p className="faint">
        What an agent chose to remember about this room, newest first. Read
        only: these are the agent's, written and removed through its own
        tools. A removed one stays visible, with who removed it.
      </p>
      {held.value.map((m: Memory) => (
        <article key={m.id} className="detail">
          <header className="row wrap">
            <span className="mono faint">{m.id}</span>
            <Pill label={m.agent} />
            {m.deleted_at && <Pill tone="bad" label="forgotten" />}
            <span className="count auto">
              {ago(m.created_at)}
            </span>
          </header>
          <p
            className="said"
            style={m.deleted_at ? { textDecoration: "line-through" } : undefined}
          >
            {m.text}
          </p>
          {m.deleted_at && (
            <p className="faint mono">removed by {m.deleted_by ?? "—"}</p>
          )}
        </article>
      ))}
    </>
  );
}

/** A real `<dialog>`, so the focus trap, the backdrop and the Escape key are
 *  the platform's rather than three things to get wrong. */
function Dialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);

  return (
    <dialog ref={ref} onClose={onClose} onCancel={onClose}>
      <header>
        <h2>{title}</h2>
        <button className="primary" onClick={onClose}>
          Close
        </button>
      </header>
      <div className="body">{children}</div>
    </dialog>
  );
}

function day(m: Message): string {
  if (!m.created_at) return "unknown day";
  const at = new Date(m.created_at);
  const today = new Date();
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (same(at, today)) return "Today";
  if (same(at, new Date(today.getTime() - 86400_000))) return "Yesterday";
  return at.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}
