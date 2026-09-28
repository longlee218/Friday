import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

/** A toast — top-right, max 3 stacked, auto-dismiss 3s, dismissable by
 *  click. Error toasts carry an action button (`Retry`, etc.) so the
 *  operator does not have to find the form again after a failure
 *  (audit #8).
 *
 *  Style lives in `index.css` under `.toast` and `.toast button.action`. */

export type Toast = {
  id: number;
  message: string;
  tone?: "good" | "warn" | "bad";
  action?: { label: string; onClick: () => void };
};

type ToastContext = {
  push: (t: Omit<Toast, "id">) => void;
};

const Ctx = createContext<ToastContext | null>(null);

const MAX_VISIBLE = 3;
const AUTO_DISMISS_MS = 3000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const push = useCallback((t: Omit<Toast, "id">) => {
    setItems((cur) => {
      const id = ++nextId.current;
      const next = [...cur, { ...t, id }];
      // Cap the visible stack. The newest stay; oldest fall off.
      return next.length > MAX_VISIBLE ? next.slice(-MAX_VISIBLE) : next;
    });
  }, []);

  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <ToastList items={items} onDismiss={(id) => setItems((cur) => cur.filter((x) => x.id !== id))} />
    </Ctx.Provider>
  );
}

function ToastList({
  items,
  onDismiss,
}: {
  items: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="toast" role="region" aria-label="Notifications">
      {items.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: (id: number) => void;
}) {
  useEffect(() => {
    const handle = window.setTimeout(() => onDismiss(toast.id), AUTO_DISMISS_MS);
    return () => window.clearTimeout(handle);
  }, [toast.id, onDismiss]);

  return (
    <div
      role={toast.tone === "bad" ? "alert" : "status"}
      onClick={() => onDismiss(toast.id)}
      className={toast.tone ? `card ${toast.tone}` : "card"}
    >
      {toast.message}
      {toast.action && (
        <button
          className="action"
          onClick={(e) => {
            e.stopPropagation();
            toast.action?.onClick();
            onDismiss(toast.id);
          }}
        >
          {toast.action.label}
        </button>
      )}
    </div>
  );
}

export function useToast(): ToastContext {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error("useToast must be used inside <ToastProvider>");
  }
  return ctx;
}
