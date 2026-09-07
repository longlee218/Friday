import { useState } from "react";

import { api } from "../api";
import type { Flow } from "../api-types";
import { useAsync } from "../useAsync";
import { CallCard, Pill, ToolCard, shortTime } from "../ui";

/** Board ticket 07 — what happened to this message.
 *
 *  Ordered steps, not a graph: every task type gets the same one-node graph,
 *  so the multi-step thing in this system is the path through the process.
 *  The two paths that matter most are the ones easiest to leave out — a
 *  `skip` (no task ever opens) and a message the prefilter held (no model
 *  call exists at all), and both render as complete outcomes rather than as
 *  a path that stops with nothing to explain it. */
export function FlowScreen({
  path,
  onOpenFlow,
}: {
  path: string;
  onOpenFlow: (provider: string, id: string) => void;
}) {
  const [, , provider, id] = path.split("/");
  const [typed, setTyped] = useState("");

  if (!provider || !id) {
    return (
      <div className="card">
        <h2>Open a path</h2>
        <p className="muted">
          Pick a message from Tasks, or type its id. A path shows everything that
          followed from one message — including the ones that produced no task.
        </p>
        <div className="row">
          <input
            type="text"
            aria-label="Message id"
            placeholder="message id"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && typed && onOpenFlow("discord", typed)}
          />
          <button
            className="primary"
            disabled={!typed}
            onClick={() => onOpenFlow("discord", typed)}
          >
            Open
          </button>
        </div>
      </div>
    );
  }

  return <Path provider={provider} id={id} />;
}

function Path({ provider, id }: { provider: string; id: string }) {
  const flow = useAsync(() => api.flow(provider, id), [provider, id]);
  // The line the confidence is judged against is `config.yaml`'s, served on
  // `/api/board`. It was hardcoded as 0.70 here — right by luck, and silently
  // wrong the first time the operator tuned it, which is the one thing this
  // badge exists to prevent.
  const board = useAsync(() => api.board(), []);
  const threshold = board.value?.confidence_threshold ?? null;

  if (flow.error) {
    return (
      <div className="card error">
        No path for {provider}/{id}: {flow.error}
      </div>
    );
  }
  if (!flow.value) return <div className="empty">Loading…</div>;

  const it: Flow = flow.value;
  const held = reasonHeld(it);

  return (
    <>
      <section className="card">
        <div className="row wrap">
          <h1>{it.message.author_name}</h1>
          <span className="faint mono">
            {it.message.provider}/{it.message.provider_message_id} ·{" "}
            {shortTime(it.message.created_at)}
          </span>
        </div>
        <pre>{it.message.text}</pre>
        {it.turn.length > 1 && (
          <details>
            {/* People send one thought in three messages, and triage reads the
                turn rather than the message — a path showing only the mention
                shows less than what was actually classified. */}
            <summary>The rest of the turn ({it.turn.length - 1} more)</summary>
            {it.turn.map((m) => (
              <pre key={m.provider_message_id}>{m.text}</pre>
            ))}
          </details>
        )}
      </section>

      <Step n={1} title="Arrived" done>
        <span className="faint mono">
          {it.message.mention_type ?? "no mention"} · {it.message.conversation}
        </span>
      </Step>

      {held && (
        <Step n={2} title="Held before the model saw it" tone="warn" done>
          <p className="mono">{held}</p>
          <p className="faint">
            A word in `sensitive_words` stops a message before any call is made.
            No prompt exists for this step because none was sent.
          </p>
        </Step>
      )}

      <Step
        n={held ? 3 : 2}
        title="Classified"
        done={it.decision !== null}
      >
        {it.decision === null ? (
          <p className="faint">
            Nothing has looked at this yet — it is still queued. That is a state,
            not a failure.
          </p>
        ) : (
          <div className="row wrap">
            <Pill label={it.decision.type ?? "—"} />
            {it.decision.confidence != null && (
              <Pill
                tone={
                  threshold == null
                    ? undefined
                    : it.decision.confidence >= threshold
                      ? "good"
                      : "warn"
                }
                label={
                  threshold == null
                    ? `confidence ${it.decision.confidence.toFixed(2)}`
                    : `confidence ${it.decision.confidence.toFixed(2)} of ${threshold.toFixed(2)}`
                }
              />
            )}
            <span className="faint mono">{shortTime(it.triaged_at)}</span>
          </div>
        )}
      </Step>

      <Step
        n={held ? 4 : 3}
        title={it.task ? `Task #${it.task.id} opened` : "No task opened"}
        done={it.decision !== null}
      >
        {it.task ? (
          <div className="row wrap">
            <Pill label={it.task.type} />
            <Pill label={it.task.state} />
          </div>
        ) : (
          <p className="faint">
            {it.decision?.type === "skip"
              ? "Skipped — social talk, salary, or off topic. Nothing to do, and that is a complete outcome."
              : "Nothing was opened for this message."}
          </p>
        )}
      </Step>

      <Step
        n={held ? 5 : 4}
        title={`Model and tool calls (${it.model_calls.length + it.tool_calls.length})`}
        done={it.model_calls.length + it.tool_calls.length > 0}
      >
        {it.model_calls.length + it.tool_calls.length === 0 ? (
          <p className="faint">No call was made about this message.</p>
        ) : (
          <>
            {/* Interleaved on time, and numbered, because the two questions
                somebody opens this for are "what did it reach for" and "why
                did it go round again" — and neither is answerable from a list
                of prompts followed by a separate list of tools. A turn is a
                model call and whatever it reached for before the next one:
                that is the unit the turn cap counts, so it is the unit shown. */}
            {turns(it).map((turn) => (
              <div key={turn.n} style={{ marginTop: 10 }}>
                <div className="row wrap">
                  <span className="pill mono">turn {turn.n}</span>
                  <span className="faint mono">{turn.agent}</span>
                  {/* `attempt` is the call's, and `CallCard` already carries
                      it — saying it twice on one row is noise, not emphasis. */}
                  {turn.unanswered && (
                    <Pill
                      tone="bad"
                      label="no answer"
                      title="sent, nothing came back — this is what a timed-out call looks like"
                    />
                  )}
                </div>
                {turn.call && <CallCard call={turn.call} />}
                {turn.tools.map((t, j) => (
                  <ToolCard key={`t${j}`} call={t} />
                ))}
              </div>
            ))}
            <p className="faint mono" style={{ marginTop: 8 }}>
              {it.model_calls.reduce(
                (n, c) => n + c.input_tokens + c.output_tokens,
                0,
              )}{" "}
              tokens over {it.model_calls.length} calls
            </p>
          </>
        )}
      </Step>

      <Step
        n={held ? 6 : 5}
        title={`Sent (${it.outbound.length})`}
        done={it.outbound.length > 0}
      >
        {it.outbound.length === 0 ? (
          <p className="faint">Nothing was queued about this message.</p>
        ) : (
          it.outbound.map((row) => (
            <div key={row.id} className="card" style={{ marginTop: 8 }}>
              <div className="row wrap">
                <Pill
                  tone={row.state === "failed" ? "bad" : undefined}
                  label={row.state}
                />
                <span className="faint mono">{row.kind}</span>
              </div>
              <pre>{row.text}</pre>
              {row.last_error && <p className="mono error">{row.last_error}</p>}
            </div>
          ))
        )}
      </Step>
    </>
  );
}

/** One turn: a model call and whatever it reached for before the next one.
 *
 *  That is the unit `max_turns` counts, so it is the unit to show. Rendering
 *  every prompt and then every tool call — which is what this did — hides
 *  both of the things somebody opens a path to find out: which call reached
 *  for what, and whether the agent went round again.
 *
 *  A call with no tokens either way is one that was sent and never answered.
 *  That is not an inference: `Harness` records `unfinished()` in a `finally`
 *  precisely so a call that hung leaves a row, and a hung call is the only
 *  way to get a row with a prompt and no usage. */
function turns(flow: Flow) {
  const tools = [...flow.tool_calls];
  return flow.model_calls.map((call, i) => {
    const next = flow.model_calls[i + 1]?.created_at;
    const mine = tools.filter(
      (t) => t.created_at >= call.created_at && (!next || t.created_at < next),
    );
    return {
      n: i + 1,
      call,
      agent: call.agent,
      attempt: call.attempt,
      retried: call.attempt > 1,
      unanswered: call.input_tokens === 0 && call.output_tokens === 0,
      tools: mine,
    };
  });
}

/** The prefilter records itself as a `needs_human` decision whose reason names
 *  the word. Without surfacing it the path just stops, which reads as the
 *  pipeline losing the message rather than as a rule doing its job. */
function reasonHeld(it: Flow): string | null {
  const reason = it.decision?.params?.["reason"];
  return typeof reason === "string" && reason.includes("not sent to the model")
    ? reason
    : null;
}

function Step({
  n,
  title,
  children,
  done,
  tone,
}: {
  n: number;
  title: string;
  children?: React.ReactNode;
  done?: boolean;
  tone?: "good" | "warn" | "bad";
}) {
  return (
    <section className="card">
      <div className="row wrap">
        {/* The number carries the order, so the sequence survives greyscale
            and a screen reader — colour is never the only signal. */}
        <span className="pill mono">{n}</span>
        <h2>{title}</h2>
        {tone && <Pill tone={tone} label={tone === "warn" ? "held" : tone} />}
        {!done && <span className="faint mono">not reached</span>}
      </div>
      <div style={{ marginTop: 6 }}>{children}</div>
    </section>
  );
}
