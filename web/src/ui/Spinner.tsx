/** A 12px spinner. Used inside buttons and table rows where the action
 *  is in flight and a skeleton would overstate how much is happening.
 *  Style lives in `index.css` under `.spinner` and `.spinner-row`. */
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-row">
      <span className="spinner" role="status" aria-label={label ?? "loading"} />
      {label && <span className="faint">{label}</span>}
    </span>
  );
}
