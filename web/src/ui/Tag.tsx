import { api } from "../api";
import { useAsync } from "../useAsync";

/** The page asks once which actions exist; every tag shares the answer. */
let registered: ReturnType<typeof api.actions> | null = null;
const actions = () =>
  (registered ??= api.actions().catch((e) => {
    registered = null; // a failed load is retried by the next tag, not kept
    throw e;
  }));

/** A task type as a label. The hue is recognised before the word is read;
 *  the word is there because nothing on this page may be knowable by colour
 *  alone. An unknown type falls back to grey rather than to no label — a
 *  type nobody styled is still a type, and hiding it would hide the task.
 *
 *  The hue is per **domain** (`backend`, `ops`), read from `/api/actions`
 *  rather than a list kept here (build-the-spine ticket 02). `skip` is core,
 *  not a domain's. Style lives in `index.css` under `.tag`. */
export function Tag({ type }: { type: string }) {
  const known = useAsync(actions, []);
  const domain = known.value?.find((a) => a.name === type)?.domain;
  const cls = type === "skip" ? "skip" : domain ? `domain-${domain}` : "unknown";
  return <span className={`tag ${cls}`}>{type}</span>;
}
