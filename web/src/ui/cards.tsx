import type { ModelCall, ToolCall } from "../api-types";
import { Pill } from "./Pill";
import { toneFor, type State } from "../flowState";

export function CallCard({ call, state }: { call: ModelCall; state: State }) {
  const t = toneFor(state);
  return (
    <article className="detail">
      <header className="row wrap">
        <strong>{call.agent}</strong>
        <span className="faint mono">{call.model}</span>
        {call.node && <Pill label={call.node} />}
        {/* The state pill is the answer to the operator's question
            "what was this step doing". Colour is decoration; the word
            is the truth — the audit's rule for every status. */}
        <Pill tone={t.tone} label={t.label} title={`turn state: ${t.label}`} />
        {call.latency_ms != null && <Pill label={`${call.latency_ms}ms`} />}
        <Pill label={`${call.input_tokens}→${call.output_tokens} tok`} />
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

export function ToolCard({ call, state }: { call: ToolCall; state: State }) {
  const t = toneFor(state);
  return (
    <article className="detail">
      <header className="row wrap">
        <strong>{call.tool}</strong>
        <span className="faint mono">{call.agent}</span>
        <Pill tone={t.tone} label={t.label} title={`tool state: ${t.label}`} />
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
