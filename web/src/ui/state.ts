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

/** States where the work is over. Their column header carries a green edge,
 *  the ones above an amber one, the rest nothing — so which column a card
 *  sits in survives being screenshotted out of its board. */
export const SETTLED = new Set(["done", "handled_by_operator"]);

/** Map a state name (the same vocabulary the Flow screen uses —
 *  done, failed, retrying, waiting, ok) to a Pill tone. `undefined`
 *  when the state is unknown, so the screen renders the pill with
 *  no decoration rather than inventing one.
 *
 *  The Monitor screen reads this for every event and every running
 *  task. The Flow screen has the same map in `flowState.ts`,
 *  separate because the Flow test is a Python mirror and this one
 *  is plain TypeScript. If a third screen needs it, lift to a
 *  single source. */
export function toneFromState(
  state: string,
): "good" | "warn" | "bad" | undefined {
  switch (state) {
    case "done":
    case "ok":
      return "good";
    case "retrying":
    case "waiting":
      return "warn";
    case "failed":
      return "bad";
    default:
      return undefined;
  }
}
