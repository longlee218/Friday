# 09: Two-file backup + WAL + startup recovery

**What to build:** A crash or restart brings back both domain state and workflow state together, and the daily backup covers both SQLite files.

**Blocked by:** 06, 07.

**Status:** ready-for-agent

- [ ] WAL is on for both the application and the DBOS system database
- [ ] A daily online backup includes both files with a retention count; the restore procedure is a documented command
- [ ] Startup recovery = DBOS resumes PENDING workflows + Friday's inbox sweep backfills from cursors
- [ ] Test covers restart recovery of an in-flight workflow
- [ ] `uv run pytest -q` passes
