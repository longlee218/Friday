Status: ready-for-agent
Blocked by: 25

# Diagnose reads Kubernetes and Loki through `devops-generic`, under the MCP's own names

Operator, 2026-09-30: "Tôi muốn cung cấp cho Diagnose có khả năng sử dụng
thêm các tool" from `devops-generic`; "Tôn trọng tên của MCP"; the MCP
tools' arguments forwarded 1:1, not limited to the room (option b).
Blocked by 25: it sets the naming rule, the argument rule and the shared
log renderer.
Amends: build-the-spine ticket 09; `docs/DESIGN.md` D4 and the toolsets rows.

## What the server has (checked 2026-09-30)

Wrapped here: `loki_query`, `loki_pod_logs`, `k8s_list_pods`,
`k8s_pod_status`, `k8s_list_events`, `k8s_read_configmap` (Secrets are not
readable by the server). `loki_query_range` is 25's.
Not wrapped: `loki_labels`, `loki_label_values`, `loki_series`,
`k8s_list_clusters` — the room's values are in the descriptions already;
add them if a run shows the model needs to discover more.
Never reachable: `release_*` writes, `vibecode_*`, `godaddy_dns_add|edit`
— each toolset declares the MCP tools it calls and the core narrows the
server to exactly them.

**The two cluster names differ.** `k8s_list_clusters`: `byteplus`,
`oregon-l40s`, `oregon-llm`, `virginia-l40s`, `vultr-ailab`. Loki's
`apero_cluster`: `byteplus`, `oregon`, `oregon-llm`, `oregon-llm-external`,
`virginia`, `vultr-ailab`, `vultr-external`. `ProdPlacement.cluster` holds
the Loki value; the hints for `k8s_*` need the other.

## What is decided

- **Same name, same arguments** as the MCP tool (25's rule), forwarded
  unchanged; no scope check.
- **Hints, not limits:** each description lists per run (`prepare=`) the
  room's services with namespace, `app`, Loki cluster and K8s cluster.
  `ProdPlacement` gains **`k8s_cluster`** (optional) for that; the operator
  fills it on the board.
- **One output shape.** `loki_*` go through 25's log renderer; the `k8s_*`
  reads get one shared renderer (a header naming the tool, cluster and
  namespace; one L-id line per pod, event or configmap key through
  `Evidence.show`; the same "capped / how to get more" notes). No MCP JSON
  reaches the model.
- **Declared explicitly** (rule: 26): Friday's descriptions and parameter
  descriptions, not the MCP's docstrings.
- **Offered only when `devops-generic` is open** this run.

## Goal

| Friday tool | Calls | Arguments (as the MCP) | Toolset |
| --- | --- | --- | --- |
| `loki_pod_logs` | `loki_pod_logs` | `namespace`, `cluster`, `pod`, `app`, `search`, `since`, `limit` | `backend.logs` |
| `loki_query` | `loki_query` | `query`, `time`, `limit` | `backend.logs` |
| `k8s_list_pods` | `k8s_list_pods` | `cluster`, `namespace` | `backend.k8s` |
| `k8s_pod_status` | `k8s_pod_status` | `cluster`, `namespace`, `pod` | `backend.k8s` |
| `k8s_list_events` | `k8s_list_events` | `cluster`, `namespace` | `backend.k8s` |
| `k8s_read_configmap` | `k8s_read_configmap` | `cluster`, `namespace`, `name` | `backend.k8s` |

- `loki_pod_logs`'s `since` is measured from **now**; `loki_query_range`
  (25) reads any window. The descriptions say which: the report's window →
  `loki_query_range`; what the service is doing now → `loki_pod_logs`.
- `backend.k8s` is granted to `trace_problem` and `backend.diagnose`.

## Acceptance

- [ ] Each tool forwards its arguments unchanged to the same-named MCP tool
      (tests on a scripted server).
- [ ] No MCP JSON reaches the model: every output goes through a shared
      renderer with L-ids (test); the `loki_*` tools render like 25's.
- [ ] Without `devops-generic` open, none is offered (test); the server is
      narrowed to exactly the declared MCP tools (test).
- [ ] Each description lists the room's services and both cluster names at
      run time; definitions identical with `__doc__` set to `None` (tests).
- [ ] `k8s_cluster` added to `ProdPlacement` and the board's service form.
- [ ] Diagnose's instructions name the tools; `backend.trace_problem` eval
      and `core.planner` run and reported.
- [ ] `docs/DESIGN.md`, `CONTEXT.md`, ticket 09 amended.
- [ ] Whole suite green; `code-review` done.
