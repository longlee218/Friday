import { useEffect, useRef, type ReactNode } from "react";

/** A real `<dialog>`. The platform implements the focus trap, the
 *  backdrop, and the Escape key — three things that are otherwise very
 *  easy to get wrong. The host (App.tsx, the screens) decides when to
 *  open it; this primitive is the markup and the close wiring.
 *
 *  Style lives in `index.css` under `dialog`. The header's "Close"
 *  button uses the `.row.between` class on the header itself rather
 *  than an inline margin. */
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  return (
    <dialog ref={ref} onClose={onClose} onCancel={onClose}>
      <header>
        <h2>{title}</h2>
        <button className="primary" onClick={onClose}>Close</button>
      </header>
      <div className="body">{children}</div>
    </dialog>
  );
}
