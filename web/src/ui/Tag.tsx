/** A task type as a label. The hue is recognised before the word is read;
 *  the word is there because nothing on this page may be knowable by colour
 *  alone. An unknown type falls back to grey rather than to no label — a
 *  type nobody styled is still a type, and hiding it would hide the task.
 *
 *  Style lives in `index.css` under `.tag` and the type-specific overrides. */
export function Tag({ type }: { type: string }) {
  const known = ["api_issue", "access_request", "doc_question", "skip"];
  return (
    <span className={`tag ${known.includes(type) ? type : "unknown"}`}>{type}</span>
  );
}
