/** A status that never rests on colour alone — it carries its own word,
 *  so it survives greyscale, colour blindness, and a screenshot in a
 *  chat. The tone is the colour, the label is the truth.
 *
 *  Style lives in `index.css` under `.pill` and `.pill.{good,warn, bad}`.
 *  Splitting it out would mean a second place to fix a contrast failure. */
export function Pill({
  label,
  tone,
  title,
}: {
  label: string;
  tone?: "good" | "warn" | "bad";
  title?: string;
}) {
  return (
    <span className={tone ? `pill ${tone}` : "pill"} title={title}>
      {label}
    </span>
  );
}
