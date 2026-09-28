import { useCallback, useEffect, useRef } from "react";

import { api } from "../api";
import type { Workflow, WorkflowState } from "../api-types";
import { useAsync } from "../useAsync";
import { useEventStream } from "../useEventStream";
import { Pill, Skeleton, ago, toneFromState } from "../ui";

/** Ticket 08 — the durable workflows, without a separate dashboard.
 *
 *  The same shape the Monitor uses: one snapshot on mount (`/api/workflows`,
 *  which reads `DBOSClient.list_workflows` — no Conductor), then the live SSE
 *  stream keeps it fresh. A `workflow` event fires once per graph node that
 *  finishes; rather than merge partial progress into the list, the panel
 *  re-asks DBOS for the authoritative snapshot — cheap on a loopback board, and
 *  it means the four buckets always agree with DBOS rather than with a guess
 *  reconstructed from events. Debounced, so a burst of node completions is one
 *  refresh, not one per node.
 *
 *  This is refetch-on-nudge, like `BoardScreen` — not the Monitor's
 *  merge-the-event-into-local-state. A `workflow` event carries a graph node's
 *  completion, a live nudge; the snapshot from `list_workflows` is the source
 *  of truth for every state, including a queued workflow no node event has
 *  touched yet, which is why the panel re-asks rather than trusting the event.
 */

//: The four buckets, in the order the operator scans them: what needs
//: watching first (in flight), what is waiting, then what is settled.
const BUCKETS: { status: WorkflowState; heading: string }[] = [
  { status: "running", heading: "Running" },
  { status: "queued", heading: "Queued" },
  { status: "failed", heading: "Failed" },
  { status: "succeeded", heading: "Succeeded" },
];

const REFRESH_DEBOUNCE_MS = 400;

export function WorkflowsScreen() {
  const snap = useAsync(() => api.workflows(), []);

  // A `workflow` event means a node finished; re-ask DBOS for the snapshot,
  // debounced so a burst of completions costs one refresh. The timer lives in
  // a ref so re-renders do not reset it, and is cleared on unmount.
  const reload = snap.reload;
  const timer = useRef<number | null>(null);
  const onEvent = useCallback(
    (ev: { type: string }) => {
      if (ev.type !== "workflow") return;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(reload, REFRESH_DEBOUNCE_MS);
    },
    [reload],
  );
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const { connected } = useEventStream("/api/events", onEvent);
  const workflows = snap.value ?? [];

  return (
    <div className="workflows">
      <header className="row wrap between">
        <Pill tone={connected ? "good" : "warn"} label={connected ? "live" : "polling"} />
        {snap.value && (
          <span className="faint mono">{workflows.length} workflows</span>
        )}
        <button onClick={snap.reload}>Refresh</button>
      </header>

      {snap.error && (
        <section className="banner">
          <Pill tone="bad" label="error" />
          <div className="grow">
            <h2>Could not load workflows</h2>
            <p className="mono error">{snap.error}</p>
          </div>
        </section>
      )}

      {!snap.value ? (
        <Skeleton height={400} />
      ) : workflows.length === 0 ? (
        <p className="faint">No workflows yet.</p>
      ) : (
        <div className="workflow-buckets">
          {BUCKETS.map((bucket) => (
            <Bucket
              key={bucket.status}
              heading={bucket.heading}
              items={workflows.filter((w) => w.status === bucket.status)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function Bucket({ heading, items }: { heading: string; items: Workflow[] }) {
  return (
    <section
      className="card"
      aria-label={`${heading} workflows`}
      aria-live="polite"
    >
      <header>
        <h2>{heading}</h2>
        <span className="faint mono">{items.length}</span>
      </header>
      {items.length === 0 ? (
        <p className="faint">None.</p>
      ) : (
        <ol className="workflow-list">
          {items.map((w) => (
            <WorkflowRow key={w.id} workflow={w} />
          ))}
        </ol>
      )}
    </section>
  );
}

function WorkflowRow({ workflow }: { workflow: Workflow }) {
  return (
    <li className="workflow-row">
      <span className="who">{workflow.name}</span>
      <span className="faint mono">{workflow.id}</span>
      <Pill tone={toneFromState(workflow.status)} label={workflow.status} />
      {workflow.queue && <span className="faint mono">{workflow.queue}</span>}
      <span className="when faint mono">{ago(workflow.updated_at)}</span>
    </li>
  );
}
