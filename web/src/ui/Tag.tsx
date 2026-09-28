/** A task type as a label. The hue is recognised before the word is read;
 *  the word is there because nothing on this page may be knowable by colour
 *  alone. An unknown type falls back to grey rather than to no label — a
 *  type nobody styled is still a type, and hiding it would hide the task.
 *
 *  Style lives in `index.css` under `.tag` and the type-specific overrides. */
export function Tag({ type }: { type: string }) {
  // Plugin task types are namespaced with a dot (`devops.api_issue`,
  // `docs.doc_question`; tickets 14/15). A dot is not a valid class-name
  // fragment, so the styled class swaps it for a dash while the label still
  // shows the full type. An unknown type falls back to grey.
  const known = ["devops.api_issue", "access_request", "docs.doc_question", "skip"];
  const cls = known.includes(type) ? type.replace(/\./g, "-") : "unknown";
  return <span className={`tag ${cls}`}>{type}</span>;
}
