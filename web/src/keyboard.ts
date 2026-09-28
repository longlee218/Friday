import { useEffect, useState } from "react";

/** Global keyboard shortcuts.
 *
 *  Vim-style: `g`-prefix keys (`g m`, `g b`, `g r`) take two
 *  strokes so they do not collide with text input. Single-key
 *  bindings (`r`, `/`, `?`, `esc`) fire on the press itself.
 *
 *  Bindings are declared as data: `id`, `label`, `key` or
 *  `prefix.key`, and a `run` callback. The hook (`useKeyboard`)
 *  registers them all on `window` and tears down on unmount.
 *  The same data also drives the `<ShortcutOverlay>`, so the
 *  table the operator reads is the table the code dispatches.
 */

export type Binding = {
  id: string;
  label: string;
  /** A single-key binding. `e.key === key` fires it. */
  key?: string;
  /** A two-stroke binding: press `prefix.prefix`, then `prefix.key`. */
  prefix?: { prefix: string; key: string };
  /** What to do when the binding fires. */
  run: () => void;
};

/** Built-in triggers the shell owns regardless of bindings:
 *  `?` toggles the overlay; `/` focuses the first input; `esc`
 *  closes any open dialog. The screen can override by registering
 *  a binding on the same key. */

const PREFIX_TIMEOUT_MS = 1000;

/** True when the focused element accepts text. Shortcuts skip
 *  while typing. */
export function isTypingTarget(): boolean {
  const t = document.activeElement as HTMLElement | null;
  if (!t) return false;
  const tag = t.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") {
    return true;
  }
  if (t.isContentEditable) return true;
  return false;
}

/** Install the global listener; return a teardown. Pure DOM —
 *  tests instantiate this directly with a fake `window`. */
export function installKeyboard(
  bindings: Binding[],
  options: {
    onToggleOverlay?: () => void;
    isInputFocused?: () => boolean;
  } = {},
): () => void {
  const onToggleOverlay = options.onToggleOverlay ?? (() => {});
  const isInputFocused = options.isInputFocused ?? isTypingTarget;

  let prefixKey: string | null = null;
  let prefixTimer: number | null = null;

  function clearPrefix() {
    prefixKey = null;
    if (prefixTimer !== null) {
      window.clearTimeout(prefixTimer);
      prefixTimer = null;
    }
  }

  function onKey(e: KeyboardEvent) {
    // Suppress while typing.
    if (isInputFocused()) return;
    // Modifiers alone are not bindings.
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    // Two-stroke: if a prefix is pending, the next printable
    // key resolves. The prefix was set on the previous press;
    // a timeout fires after a second and clears it.
    if (prefixKey !== null) {
      const want = prefixKey;
      clearPrefix();
      for (const b of bindings) {
        if (
          b.prefix &&
          b.prefix.prefix === want &&
          b.prefix.key === e.key
        ) {
          e.preventDefault();
          b.run();
          return;
        }
      }
      return;
    }

    // Shell-owned keys. They are deliberately not data — `?`,
    // `/`, and `Escape` belong to the shell, not to a screen.
    if (e.key === "?") {
      e.preventDefault();
      onToggleOverlay();
      return;
    }
    if (e.key === "/") {
      e.preventDefault();
      const first = document.querySelector<HTMLElement>(
        "main input, main textarea",
      );
      first?.focus();
      return;
    }
    // `Escape` is read by `<Dialog>` itself; the keydown here
    // is a no-op so we do not interfere with focusable handlers.

    // Prefix key — start the two-stroke window.
    if (e.key === "g") {
      prefixKey = "g";
      prefixTimer = window.setTimeout(clearPrefix, PREFIX_TIMEOUT_MS);
      return;
    }

    // Screen-registered single-key bindings.
    for (const b of bindings) {
      if (b.key === e.key) {
        e.preventDefault();
        b.run();
        return;
      }
    }
  }

  window.addEventListener("keydown", onKey);
  return () => {
    window.removeEventListener("keydown", onKey);
    clearPrefix();
  };
}

/** React hook. Re-installs on every shape change so consumers
 *  can pass a fresh `bindings` array on every render without
 *  leaking listeners. The teardown function returned by
 *  `installKeyboard` handles cleanup. */
export function useKeyboard(
  bindings: Binding[],
  options: { onToggleOverlay?: () => void } = {},
): void {
  useEffect(() => {
    return installKeyboard(bindings, options);
    // `bindings` and `options` are inline objects at most call
    // sites; a `JSON.stringify` key would defeat the point of
    // "install once per shape". The consumer memoises them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindings, options]);
}

/** One row of the overlay table. */
export interface OverlayRow {
  /** What the operator types, formatted for display ("g m", "/"). */
  keys: string;
  /** What it does. */
  label: string;
}

/** Render the bindings as the overlay table. `?` and `Escape`
 *  are shell-owned and added explicitly so the overlay reads
 *  complete without the consumer having to declare them. */
export function overlayRows(bindings: Binding[]): OverlayRow[] {
  const out: OverlayRow[] = bindings.map((b) => {
    if (b.prefix) {
      return {
        keys: `${b.prefix.prefix} ${b.prefix.key}`,
        label: b.label,
      };
    }
    return { keys: b.key ?? b.id, label: b.label };
  });
  out.push({ keys: "?", label: "Show this overlay" });
  out.push({ keys: "esc", label: "Close any open dialog" });
  out.push({ keys: "/", label: "Focus the first input" });
  return out;
}

/** The overlay's open state, lifted to the App shell so any
 *  screen can toggle it through the keyboard. */
export function useShortcutOverlay(): {
  open: boolean;
  toggle: () => void;
  close: () => void;
} {
  const [open, setOpen] = useState(false);
  return {
    open,
    toggle: () => setOpen((v) => !v),
    close: () => setOpen(false),
  };
}
