# 03: Loading states — skeleton, spinner, toast

**What to build:**

Three primitives in `web/src/ui/`:

- `Skeleton` — placeholder shaped like the eventual content. The shape
  is declared once per use site (row count, card size) and the
  primitive renders `--min-height` matching the real component.
- `Spinner` — 12px circle, used inside buttons and table rows.
- `Toast` — top-right, max 3 stacked, auto-dismiss 3s, dismissable
  by click. Supports `action: {label, onClick}` for error toasts that
  carry a Retry.

`<ToastProvider>` lives at App root. Every existing `Loading…` literal
in `web/src/screens/` is replaced with a skeleton or spinner as
appropriate.

**Blocked by:** 01, 02.

**Decisions:** D2, D3.

**Status:** ready-for-agent
