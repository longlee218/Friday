import type { ModelCall, ToolCall } from "../api-types";
import { Pill } from "./Pill";

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
