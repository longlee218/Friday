# 07: The knowledge only the operator has

**What to write:** For each channel, the routing rows ticket 01 defines, and
the service-interaction context D9 describes — identifying half structured,
reasoning half prose.

**Blocked by:** 09 (the kinds and the form to type them into).

**Revised 2026-09-17:** not a file. The knowledge is rows of kind `route`,
`service`, `project`, `dependency` and `runbook`, entered in the UI — see the
spec's "Memory: one store, twelve kinds".

**Decisions:** D3, D9.

**Status:** structured half done (2026-09-22), by the operator on
2026-09-21. The live database holds two `environment` rows, two `route`
rows, one `service` and one `project` — enough that a production domain now
resolves to a cluster, namespace and app without asking anyone, which a
real run proved. Four `fact` rows exist beside them.

**What is left:** the reasoning half D9 describes — the service-interaction
context, as prose. Still `ready-for-human`: nobody but the operator can
write it.

## Drafted for confirmation

`research/03-seed-rows.md` (2026-09-18) holds draft `project`, `service`,
`route`, `dependency` and `fact` rows from the dev cluster, production Loki
and DNS. The operator confirms or corrects them and names Midas; the
runbooks (ReelMe 500 = `ERR19`; the Midas `ERR30x` family) are theirs to
write.

## What the operator said they would supply

- Domain → pod/app per product, "nạp vào memory của channel".
- A context describing how the services interact — the ReelMe example:
  order creation calls Payment Midas; a Midas error code means check Midas
  first, and if Midas is clean, the transaction state in the database.
- Which Loki `app` is Midas (not registered with the MCP as `BE-Midas`),
  and which key joins ReelMe's log to Midas's, since correlationId does not
  cross.
- Whether Midas is shared by other products, so its knowledge lives once.

## Verify

- The shape test from ticket 01 passes on the real file.
