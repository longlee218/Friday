# Seed rows for the knowledge table, drafted 2026-09-18

Drafted from three read-only sources so the operator confirms rather than
types: dev Istio `VirtualService` hosts (`ssh dev kubectl get virtualservices
-A`, 2026-09-18), production Loki labels (`count by (apero_cluster,
namespace, app)` over 24 h, 2026-09-18), and DNS (`dig +short`,
2026-09-18). **Every row is a draft until the operator marks it.** Cells
marked `?` could not be derived.

## Findings that change the rules

- **Domain → service on dev is a table the cluster already holds**: 100+
  `VirtualService` rows in namespace `dev`. `Resolve` could read it live, but
  the knowledge table stays the source of truth — the cluster is the devops
  team's and changes without notice; the row is the operator's.
- **The domain rule has exceptions.** `api-mobile-spec-reviewer.aperogroup.ai`
  has no `.dev` and is served from namespace `dev`. `payment-service` has
  domains on both `aperogroup.ai` and `apero.vn`, resolving to *different*
  AWS endpoints. And a namespace `stg` exists (`remoria.stg.aperogroup.ai`)
  — none of the operator's projects use it, so `staging` stays out of the
  enum, but "no staging anywhere" is true of the projects, not the cluster.
- **Dev pod names are the service name plus a ReplicaSet hash**, so
  `pod_pattern` is `<service>-`. Verified for the four ReelMe pods.
- **Every production app of the eight repos lives in `oregon-llm` / `vsl`.**

## `project`

| name | repo_path | stack | notes |
|---|---|---|---|
| reelme-v2 | ~/Documents/Apero/BE-ReelMe-V2 | NestJS (`/app/dist/src` frames) | CodeGraph indexed |
| midas | ~/Documents/Apero/BE-Midas | ? | Midas = `ai-backend-reelme-payment` (operator, 2026-09-18) |
| ai-isi888 | ~/Documents/Apero/BE-AI-ISI888 | ? | CodeGraph indexed |
| chatbot-iip707 | ~/Documents/Apero/BE-AI-Chatbot-IIP707 | ? | CodeGraph indexed |
| llm-iip678 | ~/Documents/Apero/BE-LLM-IIP678 | ? | CodeGraph indexed |
| llm-isi125 | ~/Documents/Apero/AI-BE-LLM-ISI125 | ? | CodeGraph indexed |
| apero-funnel | ~/Documents/Apero/AperoFunnel | ? | prod app is `funnel-monkey` (operator, 2026-09-18) |
| apero-harness | ~/Documents/Apero/Apero-Harness | — | not a deployed service; no rows |

## `service`

| name | project | prod cluster / namespace / app | dev namespace / pod_pattern | dev ssh_host |
|---|---|---|---|---|
| backend-reelme-v2 | reelme-v2 | oregon-llm / vsl / backend-reelme-v2 | dev / `backend-reelme-v2-` | dev |
| backend-reelme | reelme-v2? (v1) | oregon-llm / vsl / backend-reelme | dev / `backend-reelme-` | dev |
| ai-backend-reelme-payment | midas | oregon-llm / vsl / ai-backend-reelme-payment | dev / `ai-backend-reelme-payment-` | dev |
| payment-service | ? (not Midas) | oregon-llm / vsl / payment-service | dev / `payment-service-` | dev |
| ai-backend-isi888 | ai-isi888 | oregon-llm / vsl / ai-backend-isi888 | dev / `ai-backend-isi888-` | dev |
| ai-backend-chatbot-iip707 | chatbot-iip707 | oregon-llm / vsl / ai-backend-chatbot-iip707 | dev / `ai-backend-chatbot-iip707-` | dev |
| ai-backend-chatbot-iip678 | llm-iip678 | oregon-llm / vsl / ai-backend-chatbot-iip678 | dev / `ai-backend-chatbot-iip678-` | dev |
| ai-backend-chatbot-isi125 | llm-isi125 | oregon-llm / vsl / ai-backend-chatbot-isi125 | dev / `ai-backend-chatbot-isi125-` | dev |
| backend-apero-funnel | apero-funnel | oregon-llm / vsl / funnel-monkey | dev / `backend-apero-funnel-` | dev |

**Midas is `ai-backend-reelme-payment`** (operator, 2026-09-18): domains
`payment-reelme.dev.aperogroup.ai` / `payment-reelme.aperogroup.ai`, repo
`BE-Midas`, production `oregon-llm/vsl/ai-backend-reelme-payment`. The
Loki app name and the repo name differ, which is exactly why this is a row
and not a rule.

**Stacks** (operator, 2026-09-18): mostly Python and Node.js, some Go.
`backend-reelme-v2` is NestJS; `ReadFailingCode`'s frame mapping needs one
rule per stack — Node `/app/dist/src/*.js:line` → `src/*.ts`, Python
tracebacks (`File "…", line N`) map directly, Go frames carry the path.
`project.stack` decides which mapper runs.

## `route`

| domain | env | service |
|---|---|---|
| api-reelme-v2.dev.aperogroup.ai | dev | backend-reelme-v2 |
| api-reelme-v2.aperogroup.ai | production | backend-reelme-v2 |
| api-reelme.dev.aperogroup.ai | dev | backend-reelme |
| api-reelme.aperogroup.ai | production | backend-reelme |
| payment-reelme.dev.aperogroup.ai | dev | ai-backend-reelme-payment |
| payment-reelme.aperogroup.ai | production | ai-backend-reelme-payment |
| payment-service.dev.aperogroup.ai | dev | payment-service |
| payment-service.aperogroup.ai | production | payment-service |
| payment-service.apero.vn | production | payment-service (different endpoint — confirm) |
| api-ai-isi888.dev.aperogroup.ai | dev | ai-backend-isi888 |
| api-ai-isi888.aperogroup.ai | production | ai-backend-isi888 |
| api-chatbot-ai-iip707.dev.aperogroup.ai | dev | ai-backend-chatbot-iip707 |
| api-chatbot-ai-iip707.aperogroup.ai | production | ai-backend-chatbot-iip707 |
| api-chatbot-ai-iip678.dev.aperogroup.ai | dev | ai-backend-chatbot-iip678 |
| api-chatbot-ai-iip678.aperogroup.ai | production | ai-backend-chatbot-iip678 |
| api-chatbot-ai-isi125.dev.aperogroup.ai | dev | ai-backend-chatbot-isi125 |
| api-chatbot-ai-isi125.aperogroup.ai | production | ai-backend-chatbot-isi125 |
| api-funnelfox.dev.aperogroup.ai | dev | backend-apero-funnel |
| api-funnelfox.aperogroup.ai | production | ? — does not resolve in DNS |

## `dependency`

| from | to | via | join_key | db_checks |
|---|---|---|---|---|
| backend-reelme-v2 | ai-backend-reelme-payment | http (`/v1/midas/intent` → Midas) and webhook back (`Midas webhook: … BILLING_ERROR` lines) | userId | see the correction below — the drafted `transactions`/`state` is wrong |

**Corrected 2026-09-21, from the real schema** (`describe_schema` on
`supermind-postgres-backend-reelme-payment`, once the db MCP was
authenticated). The draft said "`transactions` by userId, column `state`".
Both halves are wrong:

- **There is no `transactions` table.** The schema is Prisma's, PascalCase,
  and the tables that carry a payment's fate are `PSPLedgerTransaction`
  (`userId`, `status`, `purchaseId`, `subscriptionId`, `transactionId`,
  `eventType`, `retryCount`, `error` jsonb, `processedAt`), `Purchase`
  (`userId`, `status`, `productId`, `transactionId`) and `Subscription`
  (`userId`, `status`).
- **The column is `status`, not `state`** — on all three.
- `UsageTransaction` looks like a candidate by its name and is not: it is
  keyed on `walletId`/`accountId` and carries **no `userId`** at all.

So the `db_check` the operator still has to choose is between
`PSPLedgerTransaction` and `Purchase`, on `userId`, reading `status` — and
`PSPLedgerTransaction.error` is the column that would say *why*, which is
what a diagnosis is after. That is a choice about which table answers "did
this user's payment go through", and it is the operator's; the table and
column names are no longer a guess.

## `fact` (drafts, channel ReelMe)

- `ERR951` "POD preview not found" and `ERR955` "Another preview of yours is
  still rendering" on `/v1/pod/previews` are polling noise: 58,000 of
  90,000 exceptions in 30 days, up to 880 from one user in five minutes.
- Every HTTP 500 from `backend-reelme-v2` carries `ERR19`, the generic code
  (2,104 of 2,104 in 30 days).
- Midas errors surface in ReelMe's `ExceptionFilter` as `ERR30x`
  (`ERR306` 3,455 · `ERR303` 2,396 · `ERR302` 220 · `ERR300` 81 in 30 days).

## Import shape

Once ticket 09 lands, each row above is one `memories` row with
`origin='admin'`, `kind` as the heading, `key` as the first column and
`data` as the rest. Until then this file is the draft the operator edits.
