import type { CSSProperties } from "react";

/** A placeholder that shows where content will land.
 *
 *  The width and height are CSS variables set on the element so the
 *  skeleton carries no literal CSS values — the only way to honour the
 *  inline-style guard in `tests/test_web_tokens.py` (a token bypass
 *  audit) while still letting the placeholder match the real
 *  component's shape (audit #5: a shorter skeleton makes the page jump
 *  when the data arrives).
 *
 *  Use:
 *    <Skeleton width="60%" height={16} />
 *    <Skeleton h="4em" />     ← shorthand for height
 *
 *  The CSS in `index.css` reads `--sk-w` and `--sk-h` to size the
 *  placeholder; defaults apply when either is absent. */
export function Skeleton({
  width,
  height,
  h,
}: {
  width?: number | string;
  height?: number | string;
  h?: number | string;
}) {
  const style: CSSProperties = {};
  if (width !== undefined) {
    (style as Record<string, string>)["--sk-w"] =
      typeof width === "number" ? `${width}px` : width;
  }
  const finalHeight = height ?? h;
  if (finalHeight !== undefined) {
    (style as Record<string, string>)["--sk-h"] =
      typeof finalHeight === "number" ? `${finalHeight}px` : finalHeight;
  }
  return <span className="skeleton" style={style} />;
}
