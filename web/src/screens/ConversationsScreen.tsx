import { useState } from "react";

import type { Message } from "../api-types";
import { useAsync } from "../useAsync";
import { Pill, ago, room, shortTime } from "../ui";

/** What arrived, grouped by the room it arrived in.
 *
 *  It was a flat table of the newest twenty-five messages across every
 *  watched channel, newest first — so four rooms interleaved by the clock,
 *  which reads as one argument nobody is having. Grouping is the fix the
 *  operator asked for and it is also what the data already says: a
 *  `conversation` is the thread if there is one and the channel otherwise,
 *  and it is stored rather than derived precisely so it can be grouped on.
 *
 *  Within a room, consecutive messages from one person are one block and the
 *  name is said once. A name repeated five times is five things to read that
 *  say nothing — and it is the same argument as the turn: what somebody said
 *  across three messages is one thing they said. */
export function ConversationsScreen({
  onOpenFlow,
}: {
  onOpenFlow: (provider: string, id: string) => void;
}) {
  const feed = useAsync(
    () => fetch("/api/messages?limit=200").then((r) => r.json() as Promise<Message[]>),
    [],
  );
  const [open, setOpen] = useState<string | null>(null);

  if (feed.error) {
    return <div className="card error">Could not load messages: {feed.error}</div>;
  }
  if (!feed.value) return <p className="empty">Loading…</p>;
  if (feed.value.length === 0) {
    return (
      <p className="empty">
        Nothing captured yet. Start the agent and mention it in a watched
        channel.
      </p>
    );
  }

  const rooms = new Map<string, Message[]>();
  for (const m of feed.value) {
    // The API returns newest first; a conversation reads oldest first, the
    // way the people in it wrote it.
    rooms.set(m.conversation, [m, ...(rooms.get(m.conversation) ?? [])]);
  }
  const ordered = [...rooms.entries()].sort(
    (a, b) => last(b[1]).localeCompare(last(a[1])),
  );

  return (
    <>
      <div className="row wrap" style={{ justifyContent: "space-between" }}>
        <span className="faint mono">
          {feed.value.length} messages across {ordered.length}{" "}
          {ordered.length === 1 ? "room" : "rooms"}
        </span>
        <button onClick={feed.reload}>Refresh</button>
      </div>

      {ordered.map(([conversation, messages]) => (
        <Thread
          key={conversation}
          conversation={conversation}
          messages={messages}
          open={open === conversation || ordered.length === 1}
          onToggle={() => setOpen(open === conversation ? null : conversation)}
          onOpenFlow={onOpenFlow}
        />
      ))}
    </>
  );
}

function Thread({
  conversation,
  messages,
  open,
  onToggle,
  onOpenFlow,
}: {
  conversation: string;
  messages: Message[];
  open: boolean;
  onToggle: () => void;
  onOpenFlow: (provider: string, id: string) => void;
}) {
  const [all, setAll] = useState(false);
  // A room with two hundred messages, opened, buries every other room below
  // the fold — which is the clutter this screen exists to remove, produced
  // by the screen itself. The newest are the ones somebody opened it for.
  const RECENT = 12;
  const hidden = Math.max(0, messages.length - RECENT);
  const shown = all ? messages : messages.slice(-RECENT);
  const people = new Set(messages.map((m) => m.author_name));
  const newest = messages[messages.length - 1];
  const untriaged = messages.filter(
    (m) => m.mention_type !== null && !m.is_own,
  ).length;

  return (
    <section className="thread">
      <header>
        <button
          onClick={onToggle}
          aria-expanded={open}
          style={{ background: "none", border: "none", padding: 0, minHeight: 44 }}
        >
          <strong title={conversation}>{room(conversation)}</strong>
        </button>
        <span className="faint mono">
          {messages.length} messages · {people.size}{" "}
          {people.size === 1 ? "person" : "people"}
        </span>
        {untriaged > 0 && <Pill label={`${untriaged} mentioned us`} />}
        <span className="count" style={{ marginLeft: "auto" }}>
          {ago(newest.created_at)}
        </span>
      </header>

      {open && hidden > 0 && !all && (
        <div className="msg">
          <span className="who" />
          <button onClick={() => setAll(true)} style={{ justifySelf: "start" }}>
            Show {hidden} older
          </button>
          <span />
        </div>
      )}

      {open ? (
        shown.map((m, i) => (
          <div key={`${m.provider}:${m.provider_message_id}`} className="contents">
            {/* A thread that spans days shows clock times that run backwards
                — 10:41 PM then 11:33 AM — and nothing says a night passed.
                Reading that as one afternoon is the mistake this prevents. */}
            {(i === 0 || day(shown[i - 1]) !== day(m)) && (
              <div className="dayline">
                <span>{day(m)}</span>
              </div>
            )}
          <div
            className={
              // A change of *side* always starts a new block, even when the
              // name is identical — and here it usually is: replies go out
              // as the watched account, so what this system sent and what
              // the operator typed share an `author_name`. Grouping on the
              // name alone hid every "(us)" after the first, which made the
              // one distinction that matters in this feed invisible.
              i > 0 &&
              shown[i - 1].author_name === m.author_name &&
              shown[i - 1].is_own === m.is_own
                ? "msg same-author"
                : "msg"
            }
          >
            <span className="who" title={m.author_name}>
              {m.author_name}
              {m.is_own && <span className="faint"> (us)</span>}
            </span>
            <span className="said">{m.text}</span>
            <span className="when">
              <button
                onClick={() => onOpenFlow(m.provider, m.provider_message_id)}
                title="What happened to this message"
                style={{ background: "none", border: "none", padding: "0 6px" }}
              >
                {shortTime(m.created_at)}
              </button>
            </span>
          </div>
          </div>
        ))
      ) : (
        // Collapsed, a room is one line: who spoke last and what they said.
        // Enough to decide whether to open it, which is the only decision
        // being made at this level.
        <div className="msg">
          <span className="who">{newest.author_name}</span>
          <span className="said faint">{newest.text}</span>
          <span className="when">{shortTime(newest.created_at)}</span>
        </div>
      )}
    </section>
  );
}

/** The day a message was written, as somebody would say it. Used only to
 *  decide where a separator goes, so the exact wording matters less than it
 *  being stable and readable. */
function day(m: Message): string {
  if (!m.created_at) return "unknown day";
  const at = new Date(m.created_at);
  const today = new Date();
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (same(at, today)) return "Today";
  const yesterday = new Date(today.getTime() - 86400_000);
  if (same(at, yesterday)) return "Yesterday";
  return at.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

function last(messages: Message[]): string {
  return messages[messages.length - 1].created_at ?? "";
}
