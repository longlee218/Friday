import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { Memory, MemoryField, MemoryKindForm, Message, Room } from "../api-types";
import { useAsync } from "../useAsync";
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
  const [showing, setShowing] = useState<"memory" | null>(null);

  const messages = (feed.value ?? [])
    .filter((m) => m.conversation === room.id)
    .slice()
    .reverse();

  return (
    <section className="room-detail">
      <header className="card row wrap between">
        <Rename room={room} onRenamed={onRenamed} />
        <div className="row">
          <button onClick={() => setShowing("memory")}>What it remembers</button>
        </div>
      </header>

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
  const [correcting, setCorrecting] = useState<string | null>(null);
  const form = <MemoryForm channelId={channelId} onSaved={held.reload} />;

  if (held.error) return <p className="mono error">{held.error}</p>;
  if (!held.value) {
    return Array.from({ length: 3 }).map((_, i) => (
      <Skeleton key={i} height={64} />
    ));
  }
  if (held.value.length === 0) {
    return (
      <>
        {form}
        <p className="empty">
          Nothing written down about this room yet. The responder writes its
          own through its tools; what only you know goes in the form above.
        </p>
      </>
    );
  }

  return (
    <>
      {form}
      <p className="faint">
        What this room's memory holds, newest first. An agent's rows are its
        own, written and removed through its tools; yours are marked admin and
        no agent can change them. A removed one stays visible, with who
        removed it — so does a superseded one, with what it used to say and
        when it changed.
      </p>
      {held.value.map((m: Memory) => (
        <article key={m.id} className="detail">
          <header className="row wrap">
            <span className="mono faint">{m.id}</span>
            <Pill label={m.kind} />
            <Pill label={m.origin === "admin" ? "admin" : m.agent} />
            {m.key && <span className="mono">{m.key}</span>}
            {m.status === "superseded" && (
              <Pill tone="bad" label="superseded" />
            )}
            {m.deleted_at && <Pill tone="bad" label="forgotten" />}
            <span className="count auto">
              {ago(m.created_at)}
            </span>
            {m.origin === "admin" && !m.deleted_at && m.status === "active" && (
              <>
                <button
                  onClick={() => setCorrecting(correcting === m.id ? null : m.id)}
                >
                  {correcting === m.id ? "Cancel" : "Correct"}
                </button>
                <button
                  onClick={async () => {
                    await api.deleteMemory(channelId, m.id);
                    held.reload();
                  }}
                >
                  Remove
                </button>
              </>
            )}
          </header>
          {correcting === m.id && (
            <MemoryForm
              channelId={channelId}
              editing={m}
              onSaved={() => {
                setCorrecting(null);
                held.reload();
              }}
            />
          )}
          <p
            className="said"
            style={
              m.deleted_at || m.status === "superseded"
                ? { textDecoration: "line-through" }
                : undefined
            }
          >
            {m.text}
          </p>
          {m.data && <pre className="mono">{JSON.stringify(m.data, null, 2)}</pre>}
          {m.deleted_at && (
            <p className="faint mono">removed by {m.deleted_by ?? "—"}</p>
          )}
          {m.status === "superseded" && (
            <p className="faint mono">
              superseded {ago(m.updated_at)}
              {m.superseded_by ? ` by ${m.superseded_by}` : ""}
            </p>
          )}
        </article>
      ))}
    </>
  );
}

/** What only the operator knows, typed in as a row (board
 *  `read-it-the-way-the-operator-does`, ticket 09): a text area for a prose
 *  kind, one field per schema field for a structured one. The fields come
 *  from `/api/memory-kinds`, which reads them off the schemas the store
 *  checks — so a refusal names a field this form actually showed.
 *
 *  A field that names another row (`service.project`) is a dropdown over the
 *  keys this room actually holds, so the operator picks rather than spells.
 *  With no rows of that kind yet the dropdown would be an empty box with no
 *  explanation, which is the state every room starts in — so it says what is
 *  missing instead.
 *
 *  Given `editing`, the same form corrects that row in place through the PUT
 *  route: its kind and name are fixed, and its fields start from what the
 *  row holds. A name is not editable because `memory_update` takes none — a
 *  structured row's key moves with its data, and a runbook's is its own. */
function MemoryForm({
  channelId,
  onSaved,
  editing,
}: {
  channelId: string;
  onSaved: () => void;
  editing?: Memory;
}) {
  const kinds = useAsync(() => api.memoryKinds(channelId), [channelId]);
  const [kind, setKind] = useState(editing?.kind ?? "fact");
  const [text, setText] = useState(editing?.text ?? "");
  const [key, setKey] = useState("");
  // `null` until a field is touched, so a correction starts from the row's
  // own data once the kind's fields have loaded.
  const [touched, setValues] = useState<Record<string, string> | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (kinds.error) return <p className="mono error">{kinds.error}</p>;
  if (!kinds.value) return null;
  const shape: MemoryKindForm =
    kinds.value.find((k) => k.kind === kind) ?? kinds.value[0];
  const values = touched ?? (editing ? fieldValues(shape.fields, editing.data) : {});

  const save = async () => {
    setProblem(null);
    setSaving(true);
    try {
      const data = shape.prose ? undefined : payload(shape.fields, values);
      if (editing) {
        await api.updateMemory(channelId, editing.id, text, data);
      } else {
        await api.addMemory(channelId, {
          kind: shape.kind,
          text,
          key: shape.names_key ? key : undefined,
          data,
        });
      }
      setText("");
      setKey("");
      setValues(null);
      onSaved();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      className="card memory-form"
      aria-label={editing ? `Correct ${editing.id}` : "Write a memory"}
    >
      {!editing && (
        <label>
          <span className="faint">Kind</span>
          <select
            value={shape.kind}
            onChange={(e) => {
              setKind(e.target.value);
              setValues(null);
            }}
          >
            {kinds.value.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.kind}
              </option>
            ))}
          </select>
        </label>
      )}
      {!editing && shape.names_key && (
        <label>
          <span className="faint">Name</span>
          <input type="text" value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
      )}
      {shape.fields.map((f: MemoryField) => (
        <label key={f.name}>
          <span className="faint">
            {f.name}
            {f.required ? "" : " (optional)"}
            {f.type === "list" ? " — comma-separated" : ""}
            {f.type === "json" ? " — JSON" : ""}
          </span>
          {f.type === "choice" && f.names && f.choices.length === 0 ? (
            <p className="mono faint">
              no {f.names} rows in this room yet — add one first
            </p>
          ) : f.type === "choice" ? (
            <select
              value={values[f.name] ?? ""}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
            >
              <option value="" />
              {f.choices.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          ) : f.type === "json" ? (
            <textarea
              rows={3}
              value={values[f.name] ?? ""}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
            />
          ) : (
            <input
              type="text"
              value={values[f.name] ?? ""}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
            />
          )}
        </label>
      ))}
      <label>
        <span className="faint">
          {shape.prose
            ? "What is true here"
            : shape.names_key
              ? "The steps, in words"
              : "Note (optional)"}
        </span>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      {problem && <p className="mono error">{problem}</p>}
      <div>
        <button className="primary" disabled={saving} onClick={save}>
          {editing ? "Save correction" : "Remember it"}
        </button>
      </div>
    </section>
  );
}

/** `payload` read backwards: a row's stored data as the form's strings, so
 *  a correction starts from what is there rather than from blank fields. */
function fieldValues(fields: MemoryField[], data: unknown) {
  const out: Record<string, string> = {};
  for (const f of fields) {
    let at: unknown = data;
    for (const part of f.name.split(".")) {
      at = at && typeof at === "object" ? (at as Record<string, unknown>)[part] : undefined;
    }
    if (at === undefined || at === null) continue;
    out[f.name] =
      f.type === "json"
        ? JSON.stringify(at, null, 2)
        : Array.isArray(at)
          ? at.join(", ")
          : String(at);
  }
  return out;
}

/** The form's strings as the object the kind's schema expects: dotted names
 *  nested, lists split on commas, numbers parsed, JSON parsed. An optional
 *  field left empty is left out; a required one is sent empty, so the
 *  store's refusal names it. */
function payload(fields: MemoryField[], values: Record<string, string>) {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const raw = (values[f.name] ?? "").trim();
    if (!raw && !f.required) continue;
    const value =
      f.type === "list"
        ? raw.split(",").map((v) => v.trim()).filter(Boolean)
        : f.type === "number"
          ? Number(raw)
          : f.type === "json"
            ? JSON.parse(raw || "null")
            : raw;
    const path = f.name.split(".");
    let at = out;
    for (const part of path.slice(0, -1)) {
      at = (at[part] ??= {}) as Record<string, unknown>;
    }
    at[path[path.length - 1]] = value;
  }
  return out;
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
