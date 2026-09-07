import { useEffect, useState } from "react";

import { BoardScreen } from "./screens/BoardScreen";
import { ContextScreen } from "./screens/ContextScreen";
import { ConversationsScreen } from "./screens/ConversationsScreen";
import { FlowScreen } from "./screens/FlowScreen";

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

//: In the order somebody works: what is outstanding, what was said, what one
//: message did, what this room is told. `Path` is last because it is reached
//: by clicking a card far more often than by typing an id.
const TABS = [
  { path: "/", label: "Board" },
  { path: "/rooms", label: "Rooms" },
  { path: "/context", label: "Context" },
  { path: "/flow", label: "Path" },
];

export function App() {
  const [path, go] = useRoute();
  const section = TABS.map((t) => t.path)
    .filter((p) => p !== "/")
    .find((p) => path.startsWith(p)) ?? "/";

  const openFlow = (provider: string, id: string) => go(`/flow/${provider}/${id}`);

  return (
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
        {section === "/" && <BoardScreen onOpenFlow={openFlow} />}
        {section === "/rooms" && <ConversationsScreen onOpenFlow={openFlow} />}
        {section === "/context" && <ContextScreen />}
        {section === "/flow" && <FlowScreen path={path} onOpenFlow={openFlow} />}
      </main>
    </div>
  );
}
