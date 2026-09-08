# 03: Loading states — skeleton, spinner, toast

**What to build:**

The audit's note: "a screen that says 'Loading…' with no shape is
a screen that has no idea what is coming". Every screen renders
a Skeleton whose shape matches the content it stands in for, and
the literal `Loading…` text is gone.

**Primitives:**

- `Skeleton` — placeholder shaped like the eventual content. The
  shape is declared once per use site (row count, card size) and
  the primitive renders `--min-height` matching the real
  component. Width and height are CSS variables on the element
  (`--sk-w` / `--sk-h`), so the placeholder carries no literal
  CSS values and the inline-style guard stays clean.
- `Spinner` — 12px circle, used inside buttons and table rows.
  Has `role="status"` and an `aria-label` so screen readers
  announce the in-flight state.
- `Toast` — top-right, max 3 stacked, auto-dismiss 3s, dismissable
  by click. Supports `action: {label, onClick}` for error toasts
  that carry a Retry.
- `<ToastProvider>` lives at App root. Every existing `Loading…`
  literal in `web/src/screens/` is replaced with a Skeleton or
  Spinner as appropriate.

**Blocked by:** 01, 02.

**Decisions:** D2, D3.

**Status:** done

- [ ] `Skeleton`, `Spinner`, `Toast` exported from
      `web/src/ui/`. `Skeleton` takes `width` and `height` as
      props and renders `--sk-w` / `--sk-h`. `Spinner` renders
      `role="status"` with `aria-label`. `Toast` exposes a
      `<ToastProvider>` and `useToast()` hook.
- [ ] `App.tsx` wraps the shell in `<ToastProvider>`. Every
      screen that needs a toast calls `useToast()`.
- [ ] Every `Loading…` literal in `web/src/screens/**` is
      replaced with a Skeleton whose height matches the eventual
      content (audit #5). Shapes per screen: Board 5 rows × 80px,
      Rooms sidebar 4 × 44px, Rooms messages 6 × 48px, Memory 3 ×
      64px, Context header 1 × 20px + body 4 × 28px, Flow 5 ×
      56px.
- [ ] `tests/test_web_tokens.py` mutation-tested: re-introducing
      a `Loading…` literal anywhere in `web/src/screens/**` flips
      a guard red. The Skeleton primitive renders a real
      element with the `skeleton` class, not a div with text.
- [ ] `tests/test_web_tokens.py::test_app_root_wires_toast_provider`
      pins `<ToastProvider>` at App root. Without it, `useToast`
      throws.
