
/** One item on the breadcrumb trail.
 *
 *  `href` is what the user clicks; `label` is what they see.
 *  `current` flags the last item, which renders as plain text
 *  rather than a link — you cannot click where you already are,
 *  and rendering it as a link is the audit's "two things that
 *  look the same but aren't" failure. */
export interface BreadcrumbItem {
  label: string;
  href?: string;
  current?: boolean;
}

const MAX_ITEMS = 4;

/** A breadcrumb trail, monospace.

 *  The separator is `›` (a single right-pointing angle quotation
 *  mark), which is what the audit's reading-aid research calls
 *  for: a real glyph that screen readers can announce ("greater
 *  than") rather than a CSS-drawn slash that announces nothing.
 *
 *  Anything beyond `MAX_ITEMS` items collapses to "… › item-3 ›
 *  item-4", so the trail never overflows the page header on a
 *  four-item-or-deeper path. */
export function Breadcrumb({ items }: { items: BreadcrumbItem[] }) {
  const visible = collapse(items, MAX_ITEMS);
  return (
    <nav className="breadcrumb mono" aria-label="Path">
      {visible.map((item, i) => (
        <span key={i} className="crumb">
          {i > 0 && <span className="sep" aria-hidden="true">›</span>}
          {item.href && !item.current ? (
            <a href={item.href}>{item.label}</a>
          ) : (
            <span className="crumb-current" aria-current={item.current ? "page" : undefined}>
              {item.label}
            </span>
          )}
        </span>
      ))}
    </nav>
  );
}

function collapse(items: BreadcrumbItem[], max: number): BreadcrumbItem[] {
  if (items.length <= max) return items;
  const keep_last = max - 2;
  const head = items.slice(0, 1);
  const tail = items.slice(items.length - keep_last);
  return [...head, { label: "…" }, ...tail];
}

/** A friendly name for a deep path test that does not duplicate
 *  the runtime — the test imports the function directly. */
export const __test__ = { collapse, MAX_ITEMS };
