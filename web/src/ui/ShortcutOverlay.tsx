import { Dialog } from "./Dialog";
import { overlayRows, type Binding } from "../keyboard";

/** The shortcut overlay — a Dialog listing every binding on the
 *  page. The data drives both the dispatch (in `installKeyboard`)
 *  and the display (in this component), so the operator's view
 *  cannot drift from the code's behaviour.
 *
 *  Dismissable by `?`, `esc`, or click-outside — `?` is handled
 *  by `installKeyboard` calling `onToggleOverlay`; `esc` and
 *  click-outside are the Dialog primitive's own. */
export function ShortcutOverlay({
  bindings,
  open,
  onClose,
}: {
  bindings: Binding[];
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  const rows = overlayRows(bindings);
  return (
    <Dialog title="Keyboard shortcuts" onClose={onClose}>
      <table className="shortcuts">
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.keys}-${r.label}`}>
              <td>
                <kbd>{r.keys}</kbd>
              </td>
              <td>{r.label}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Dialog>
  );
}
