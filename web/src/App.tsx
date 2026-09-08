import { useEffect, useMemo, useState } from "react";

import { BoardScreen } from "./screens/BoardScreen";
import { FlowScreen } from "./screens/FlowScreen";
import { MonitorScreen } from "./screens/MonitorScreen";
import { RoomsScreen } from "./screens/RoomsScreen";
import {
  ShortcutOverlay,
  ToastProvider,
} from "./ui";
import { useKeyboard, useShortcutOverlay, type Binding } from "./keyboard";

/** Real paths, not a hash — the server answers any unknown path with
 *  `index.html` (see `_mount_page`), so a flow can be linked to and reloaded.
 *  Twenty lines instead of a router dependency: there are three screens. */
export function useRoute(): [string, (to: string) => void] {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = (to: string) => {
    window.history.pushState({}, "", to);
    setPath(to);
  };
  return [path, go];
}

/** Module-level navigate. The hook above owns the React-side
 *  state; this is the imperative shim for components that have a
 *  click but no hook in scope (a feed row that lives inside a
 *  memo'd list). The setPath is skipped — a screen that does
 *  not re-read path on every render would miss the route change,
 *  but every screen here does. */
export function navigate(to: string): void {
  window.history.pushState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

//: Two, because there are two questions: what is outstanding, and what was
//: said. Context and memory moved inside a room, where they belong — they
//: are a room's own knowledge and reaching them used to mean leaving it.
//:
//: `Path` was a third tab and is gone. It asked you to know a message id
//: before it showed you anything, which is why nobody could say what it was
//: for. It is what a timestamp does now: click when a message was sent and
//: you get what happened because of it.
const TABS = [
  { path: "/", label: "Monitor" },
  { path: "/board", label: "Board" },
  { path: "/rooms", label: "Rooms" },
];

export function App() {
  const [path, go] = useRoute();
  // `/flow/...` is reachable and linkable but is not a tab: it is where a
  // timestamp takes you, not a place to start.
  const section = ["/board", "/rooms", "/flow"].find((p) =>
    p === "/" ? path === "/" : path.startsWith(p),
  ) ?? "/";

  const openFlow = (provider: string, id: string) => go(`/flow/${provider}/${id}`);

  // Keyboard shortcuts. The bindings table is also the
  // `<ShortcutOverlay>`'s display data — same source, no drift.
  const overlay = useShortcutOverlay();
  const bindings = useMemo<Binding[]>(
    () => [
      { id: "monitor", label: "Monitor", prefix: { prefix: "g", key: "m" }, run: () => go("/") },
      { id: "board", label: "Board", prefix: { prefix: "g", key: "b" }, run: () => go("/board") },
      { id: "rooms", label: "Rooms", prefix: { prefix: "g", key: "r" }, run: () => go("/rooms") },
      { id: "reload", label: "Reload this screen", key: "r", run: () => window.location.reload() },
      // `?`, `/`, `esc` are shell-owned and read by `installKeyboard`
      // directly; the overlay lists them through `overlayRows()`.
    ],
    [go],
  );
  useKeyboard(bindings, { onToggleOverlay: overlay.toggle });

  return (
    <ToastProvider>
      <div className="shell">
        <header className="topbar">
          <span className="brand">friday</span>
          <nav className="tabs" aria-label="Sections">
            {TABS.map((tab) => (
              <button
                key={tab.path}
                className="tab"
                aria-current={section === tab.path ? "page" : undefined}
                onClick={() => go(tab.path)}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </header>
        <main>
          {/* `key={section}` remounts on route change so the `fade-enter`
              keyframe runs every time. The DOM node is replaced, the
              animation starts from `opacity: 0`, and `prefers-reduced-
              motion: reduce` collapses the duration to 0 — the new
              screen appears instantly, which is what the audit
              asked for in note #1 (motion that respects the user's
              own setting). The cost of the remount is one element
              per route change, well below the threshold of
              "expensive enough to memo". */}
          <div key={section} className="fade-enter">
            {section === "/" && <MonitorScreen />}
            {section === "/board" && <BoardScreen onOpenFlow={openFlow} />}
            {section === "/rooms" && <RoomsScreen onOpenFlow={openFlow} />}
            {section === "/flow" && <FlowScreen path={path} onOpenFlow={openFlow} />}
          </div>
        </main>
      </div>
      <ShortcutOverlay
        bindings={bindings}
        open={overlay.open}
        onClose={overlay.close}
      />
    </ToastProvider>
  );
}
