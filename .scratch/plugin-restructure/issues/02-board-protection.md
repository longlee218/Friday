# 02: Board protection

**What to build:** The board can no longer be written to by anything but the operator's own session — a write that lacks the session CSRF token or carries a foreign Host/Origin is refused, and `BOARD_TOKEN` is gone.

**Blocked by:** 01.

**Source:** `spec.md` — Migration order, step 1 (library-independent defects); § Implementation Decisions → "Defects".

**Status:** done

- [x] `BOARD_TOKEN` removed from code and config (no route ever checked it; it only lifted the loopback refusal)
- [x] Every board write route checks the session CSRF token
- [x] Exact Host and Origin checked against an allow-list; a foreign Host is refused
- [x] A random session secret is minted at startup; cookie is `SameSite=Strict`
- [x] A write without the token, or with a foreign Host, is refused (guard deleted once and watched go red)
- [x] `uv run pytest -q` passes
