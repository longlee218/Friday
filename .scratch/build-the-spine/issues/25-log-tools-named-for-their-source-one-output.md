Status: ready-for-agent
Blocked by:

# Log tools named for their source: `loki_query_range`, `ssh_read_log` — one output

Third of the tool tickets (see [23](23-core-repos-read-grep-glob-at-the-running-tag.md)).
Amends: build-the-spine ticket 09; `docs/DESIGN.md` D4 (the two ways to
read a log become two tools).
Source: operator design review, 2026-09-30:
- `read_log` must reach "môi trường nào, pod, namespace, cluster nào … để
  khớp với MCP";
- "Tôi không muốn tool read_log bị overload … cô lập các logic và tăng khả
  năng chính xác mô tả, kèm hints của các args input. Tất nhiên các tool
  này nên cùng trả 1 định dạng để model không bị noise";
- "Tôn trọng tên của MCP": a wrapper takes the MCP tool's exact name;
- "tại sao lại không forward biến của MCP ra dùng cho đúng" → option (b):
  forward the MCP tool's arguments 1:1, not limited to the room.

## The problem

`read_log(needle, minutes_back)` picks its source from the placement's env
(`logs.py:461`), builds the selector itself from the one resolved service,
and cannot read another. One description covers two back ends with
different reach, retention and syntax.

## What is decided

- **Named for what it calls.** A wrapper of an MCP tool has that tool's
  name and calls it only: `loki_query_range` → `devops-generic`'s
  `loki_query_range`. Friday's own reader is `<transport>_<verb>_<thing>`:
  `ssh_read_log`. A later source is a new tool beside them.
- **The MCP tool's arguments, forwarded 1:1** (operator: option b). Same
  names, types and defaults as the MCP schema: `loki_query_range(query,
  start, end, limit, direction)`. The model writes the LogQL. **No scope
  check**: any namespace or cluster the server allows. `ssh_read_log` has
  no MCP schema to copy, so it takes `loki_pod_logs`'s shape for the same
  job: `ssh_read_log(namespace, pod, search, since, limit, host)`.
- **The room's values are hints, not limits.** Each description lists, per
  run (`prepare=`), the room's services with their namespace, `app`, Loki
  `apero_cluster` and dev `pod_pattern`, and the report's time — so the
  model has the right values without discovering them.
- **One output format for every log tool.** Each source returns the same
  value (today's `Lines`: lines with time and stream, and the oldest line
  reached); one shared renderer turns it into what the model reads: a
  header (tool, window), L-id lines through `Evidence.show`, the error-code
  histogram, the same "out of reach", "capped" and "how to get more" notes.
  No MCP JSON reaches the model; a source never formats its own text.
- **Offered only when it can run:** no `devops-generic` this run → no
  `loki_query_range`; no SSH host → no `ssh_read_log`.
- Both stay in `backend.logs` (one grant unit). The toolset declares the
  MCP tools it calls; the core narrows the server to exactly them.
- **Declared explicitly, never from a docstring** (rule: 26): the MCP's own
  parameter descriptions are not passed through; each parameter's
  description is Friday's, rendered from constants.

## Goal

| Tool | Calls | Arguments (as the MCP / its shape) |
| --- | --- | --- |
| `loki_query_range` | MCP `loki_query_range` | `query`, `start`, `end`, `limit`, `direction` |
| `ssh_read_log` | `kubectl logs` over SSH | `namespace`, `pod`, `search`, `since`, `limit`, `host` |

- `Placement` carries the room's services (outside `IDENTITY`, like
  `projects`) for the hints.
- `read_log`, `LokiSource`'s selector building, and the duplicated
  `_rfc3339`/`_text_of` in `logs.py` go.

## Out of scope

The other `devops-generic` reads (28); an Elasticsearch source; a scope
check (decided against).

## Acceptance

- [ ] `loki_query_range` forwards its arguments unchanged to the MCP tool of
      the same name (test on a scripted server).
- [ ] `ssh_read_log` reads the named namespace/pod on the named host (test).
- [ ] Both render the same fixture lines to the same text apart from the
      header (test); a test fails if a log tool formats its own output.
- [ ] A tool whose source is not configured is not offered (test).
- [ ] Each description lists the room's services and values at run time,
      and each definition is identical with `__doc__` set to `None` (tests).
- [ ] `tests/test_investigate_tools.py` and `tests/test_tools.py` updated;
      diagnose's instructions name both tools; `backend.trace_problem` eval
      run and reported.
- [ ] `docs/DESIGN.md` D4, `CONTEXT.md`, ticket 09 amended.
- [ ] Whole suite green; `code-review` done.
