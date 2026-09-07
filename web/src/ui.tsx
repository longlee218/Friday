import type { ModelCall, ToolCall } from "./api-types";

/** The pieces every screen shares.
 *
 *  Their own module because `FlowScreen` was importing them from
 *  `TasksScreen` — a screen depending on a sibling screen, which makes the
 *  second one impossible to delete and the first one impossible to read
 *  without opening it. */

export function Pill({
  label,
  tone,
  title,
}: {
  label: string;
  tone?: "good" | "warn" | "bad";
  title?: string;
}) {
  return (
    <span className={tone ? `pill ${tone}` : "pill"} title={title}>
      {label}
    </span>
  );
}

export function shortTime(iso: string | null): string {
  if (!iso) return "—";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleTimeString();
}

/** How long ago, in the coarsest unit that is still true.
 *
 *  "3h" rather than "14:22" wherever the question is *how stale is this* —
 *  which is most places here, and is the question `max_message_age` made
 *  load-bearing. A clock time makes the reader do the subtraction. */
export function ago(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

/** A channel id is not a name a person recognises. Show the tail, which is
 *  the part that differs, and keep the whole thing in the title. */
export function room(conversation: string): string {
  const [, place = conversation] = conversation.split(":");
  // A Discord channel id is nineteen digits and means nothing to anybody.
  // Nothing in this system holds a channel *name* — the provider is never
  // asked for one — so the best available is a short stable tail that can
  // be told apart at a glance, with the whole id in the `title`.
  if (/^\d{12,}$/.test(place)) return `#${place.slice(-6)}`;
  return place.length > 22 ? `…${place.slice(-20)}` : place;
}

export function CallCard({ call }: { call: ModelCall }) {
  return (
    <article className="detail">
      <header className="row wrap">
        <strong>{call.agent}</strong>
        <span className="faint mono">{call.model}</span>
        {call.node && <Pill label={call.node} />}
        {call.latency_ms != null && <Pill label={`${call.latency_ms}ms`} />}
        <Pill label={`${call.input_tokens}→${call.output_tokens} tok`} />
        {/* A retry is the cheapest early sign a provider is struggling, so
            it is visible without opening anything. */}
        {call.attempt > 1 && <Pill tone="warn" label={`attempt ${call.attempt}`} />}
      </header>
      <details>
        <summary>Prompt</summary>
        <pre>{call.system_prompt}</pre>
        <pre>{call.prompt}</pre>
      </details>
      <details>
        <summary>Answer</summary>
        <pre>{call.output}</pre>
      </details>
    </article>
  );
}

export function ToolCard({ call }: { call: ToolCall }) {
  return (
    <article className="detail">
      <header className="row wrap">
        <strong>{call.tool}</strong>
        <span className="faint mono">{call.agent}</span>
        {/* `failed` carries its own word. A tool failure is turned into an
            ordinary-looking message for the model, so this flag is the only
            thing that distinguishes it from an answer — and colour alone
            would hide that from anyone who cannot separate red from grey. */}
        <Pill tone={call.failed ? "bad" : "good"} label={call.failed ? "failed" : "ok"} />
        {call.latency_ms != null && <Pill label={`${call.latency_ms}ms`} />}
      </header>
      <details>
        <summary>Arguments and result</summary>
        <pre>{call.arguments}</pre>
        <pre>{call.result}</pre>
      </details>
    </article>
  );
}

/** One word per task state, in the operator's language rather than the
 *  enum's. The enum value stays the column's `title` so nothing is lost. */
export const STATE_LABEL: Record<string, string> = {
  pending: "To work on",
  waiting_for_details: "Waiting on them",
  needs_human: "Needs you",
  review: "Awaiting approval",
  done: "Done",
  handled_by_operator: "You handled it",
};

/** Which columns carry an obligation. Only these are open by default: a
 *  board that shows six equal columns makes the reader find the two that
 *  matter, every time they look. */
export const DEMANDS_ATTENTION = new Set(["needs_human", "review"]);
