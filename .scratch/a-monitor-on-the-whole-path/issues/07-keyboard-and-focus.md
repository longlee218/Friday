# 07: Keyboard and focus

**What to build:**

Power-user shortcuts so the operator can drive the whole dashboard
without leaving the keyboard. The shortcut overlay lists every binding
in one place; the keybinding table itself is small and stable.

Bindings:

- `g m` → `/monitor`
- `g b` → `/board`
- `g r` → `/rooms`
- `j` / `k` → next / prev task on the board (when the board has
  focus)
- `r` → reload current screen's data
- `/` → focus first input on the screen
- `?` → open shortcut overlay
- `esc` → close dialog, return focus to the trigger

The `g`-prefix is a two-stroke sequence (`g` then `m`) so single-key
shortcuts do not collide with text input. Vim-style; matches what
the audit called for as the operator's natural habit.

**Blocked by:** 04.

**Decisions:** D1.

**Status:** ready-for-agent

- [ ] `web/src/keyboard.ts` registers the bindings on `window` and
      exposes a hook for the screen that wants to handle `r` (the
      current screen's reload). The hook reads the active screen
      from `useRoute()` so the same keybinding file works for all
      three screens without prop-drilling.
- [ ] `<ShortcutOverlay>` is a Dialog listing every binding with
      its key in a `<kbd>` and its description next to it.
      Dismissable by `?`, `esc`, or click-outside. The list is the
      same on every screen — bindings do not depend on context.
- [ ] Every interactive element renders a visible focus ring using
      `--accent` at 2px outline + 2px offset. The existing
      `:focus-visible` rule in `index.css` already does this; a
      guard confirms it survives future component rewrites.
- [ ] `g m` / `g b` / `g r` navigate. `r` calls the current screen's
      reload (the same callback `useAsync` exposes). `?` opens and
      closes the overlay. `esc` closes the dialog and returns focus
      to the element that opened it.
- [ ] `tests/test_web_tokens.py` pins the keybinding table — the
      exact keys and labels that the overlay renders. A regression
      that drops one fails the build before the operator notices.
- [ ] `tests/test_keyboard.py` (or `test_web_tokens.py`) verifies
      the `g`-prefix sequence with a fake event source: pressing
      `g` then `m` within the timeout window navigates; pressing
      `g` then `x` does not.
