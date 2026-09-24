# 09: Two-file backup + WAL + startup recovery

**What to build:** A crash or restart brings back both domain state and workflow state together, and the daily backup covers both SQLite files.

**Blocked by:** 06, 07.

**Source:** `spec.md` — § Local operation (§12.1).

**Status:** done

- [x] WAL is on for both the application and the DBOS system database
- [x] A daily online backup includes both files with a retention count; the restore procedure is a documented command
- [x] Startup recovery = DBOS resumes PENDING workflows + Friday's inbox sweep backfills from cursors
- [x] Test covers restart recovery of an in-flight workflow
- [x] Clean code: remove the dead code, outdated comments and now-unused imports/functions this change leaves behind, and reconcile the modules it touched against the new `sdk`/`kernel`/`plugins` structure — nothing left in the old shape
- [x] `uv run pytest -q` passes
