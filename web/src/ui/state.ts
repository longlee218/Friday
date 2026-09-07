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
