import { useEffect, useState } from "react";

import { ContextScreen } from "./screens/ContextScreen";
import { FlowScreen } from "./screens/FlowScreen";
import { TasksScreen } from "./screens/TasksScreen";

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

const TABS = [
  { path: "/", label: "Tasks" },
  { path: "/flow", label: "Flow" },
  { path: "/context", label: "Context" },
];

export function App() {
  const [path, go] = useRoute();
  const section = path.startsWith("/context")
    ? "/context"
    : path.startsWith("/flow")
      ? "/flow"
      : "/";

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
        {section === "/" && <TasksScreen onOpenFlow={(p, id) => go(`/flow/${p}/${id}`)} />}
        {section === "/flow" && (
          <FlowScreen path={path} onOpenFlow={(p, id) => go(`/flow/${p}/${id}`)} />
        )}
        {section === "/context" && <ContextScreen />}
      </main>
    </div>
  );
}
