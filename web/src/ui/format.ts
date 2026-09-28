/** Time and identifier helpers used across screens. */

export function shortTime(iso: string | null): string {
  if (!iso) return "—";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleTimeString();
}

/** How long ago, in the coarsest unit that is still true.
 *
 *  "3h" rather than "14:22" wherever the question is *how stale is this* —
 *  which is most places here, and is the question `max_message_age` made
 *  load-bearing. A clock time makes the reader do the subtraction. */
export function ago(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

/** A channel id is not a name a person recognises. Show the tail, which is
 *  the part that differs, and keep the whole thing in the title. */
export function room(conversation: string): string {
  const [, place = conversation] = conversation.split(":");
  // A Discord channel id is nineteen digits and means nothing to anybody.
  // Nothing in this system holds a channel *name* — the provider is never
  // asked for one — so the best available is a short stable tail that can
  // be told apart at a glance, with the whole id in the `title`.
  if (/^\d{12,}$/.test(place)) return `#${place.slice(-6)}`;
  return place.length > 22 ? `…${place.slice(-20)}` : place;
}

/** What a room is called, or the least-bad thing to call it.
 *
 *  The operator's name first — that is what naming is for. Then the channel
 *  if it reads as a word, then a short stable tail of the id, because
 *  nineteen digits is not a name and nothing in this system holds a real
 *  one: the provider is never asked. */
export function roomName(
  conversation: string,
  name?: string | null,
): string {
  if (name) return name;
  const [, place = conversation] = conversation.split(":");
  if (/^\d{12,}$/.test(place)) return `#${place.slice(-6)}`;
  return place.length > 24 ? `…${place.slice(-22)}` : place;
}
