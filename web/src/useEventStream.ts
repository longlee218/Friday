import { useEffect, useRef, useState } from "react";

/** An event from `/api/events`, after the wire has unwrapped it.
 *
 *  The SSE endpoint carries the same shape `_format_sse` writes:
 *  `id`, `type`, `occurred_at`, `payload`. The hook keeps the
 *  shape the React side reads (a flat object, not the SSE wire
 *  form) so screens never see the protocol.
 */
export interface ServerEvent {
  id: number;
  type: string;
  occurred_at: string;
  payload: Record<string, unknown>;
}

/** A live event stream from the server.
 *
 *  Wraps the browser's native `EventSource`. Reconnect is the
 *  browser's job — `EventSource` carries the `Last-Event-ID`
 *  header itself and reconnects with backoff after a network
 *  drop, so this hook does not reimplement any of it.
 *
 *  The hook opens the connection on mount, closes it on unmount,
 *  and keeps the most recent event id in a ref so a re-render of
 *  the consumer does not re-create the connection (which would
 *  double the in-flight connections on every state update). */
export function useEventStream(
  url: string,
  onEvent: (e: ServerEvent) => void,
): { connected: boolean } {
  const [connected, setConnected] = useState(false);
  // Keep `onEvent` in a ref so the effect's identity does not
  // depend on the consumer's render — otherwise the connect would
  // tear down on every state change, which is exactly the bug SSE
  // is supposed to fix.
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const source = new EventSource(url);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    // `onmessage` would receive events with no `event:` field
    // (the SSE default). Our endpoint always sends a `type`, so
    // we listen on `source.addEventListener("message", ...)` for
    // every event — the `type` field carries the discriminator.
    source.addEventListener("message", (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data);
        handlerRef.current(data as ServerEvent);
      } catch {
        // The endpoint emits JSON. A parse error is a server bug;
        // dropping it is safer than rendering half of an event.
      }
    });
    return () => {
      source.close();
      setConnected(false);
    };
  }, [url]);

  return { connected };
}
