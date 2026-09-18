# 11: One invoke in the runner

**What to build:** `DAGRunner._invoke` — per-node timeout, per-node retry
with an explicit exception list, exception → envelope, a `node_runs` row per
attempt — the `status` envelope, and `dag_version` in the checkpoint key.

**Blocked by:** nothing. **Decisions:** spec, "Architecture v3"; finding E.
**Status:** ready-for-agent

## Verify
- Whole suite. A node that hangs returns `timed_out` and the run reaches its
  last node; a node raising a listed exception retries, an unlisted one does
  not; a renamed node does not inherit a stored result.
- Config load refuses a model node whose timeout is below its harness's.
