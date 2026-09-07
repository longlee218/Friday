# 07: Keyboard and focus

**What to build:**

`web/src/keyboard.ts` registers global keybindings on `window`:

- `g m` → `/monitor`
- `g b` → `/board`
- `g r` → `/rooms`
- `j` / `k` → next / prev task on the board
- `r` → reload current screen's data
- `/` → focus first input on the screen
- `?` → open shortcut overlay
- `esc` → close dialog, return focus to trigger

The shortcut overlay (`<ShortcutOverlay>`) is a Dialog listing every
binding with its key, dismissable by `?` or `esc` or click-outside.

Every interactive element gets a visible focus ring using `--accent`.
The token for the focus ring (`--focus-ring: 0 0 0 2px var(--accent)`)
is in `tokens.css` and overridden to 0 on `prefers-reduced-motion` only
for transitions; focus ring stays.

A test asserts the keybinding table: open the overlay, count rows,
each row has a `<kbd>` and a description.

**Blocked by:** 04.

**Decisions:** D1.

**Status:** ready-for-agent
