# 03: Dev logs through SSH to the dev host

**What to build:** A `friday/tools/` read tool wrapping `kubectl get pods`
and `kubectl logs`, and the dev branch of the log node.

**Blocked by:** nothing (2026-09-22).

**Decisions:** D4, D5, D14.

**Status:** done in a different shape (2026-09-22). `friday/tools/` exists
and is where an *agent's* tools live, but `kubectl` is not among them and
should not be: reading is `friday/sources/logs.py`'s `SshKubectlSource`,
under the Source layer the operator asked for on
2026-09-21 ("node chuyên về kết nối SSH, node chuyên về Kubectl"), and the
dev branch of the log node picks it whenever the environment is not
production.

Two faults found by running it rather than by reading it: `--tail` counts
from the newest line and there is no `--until`, so a sixteen-hour-old
window came back as today's newest 400 lines (fixed with `--timestamps` and
client-side clipping); and `--tail` is applied before anything downstream
sees a line, so narrowing now asks for the whole window and lets `grep`
cut it on the far side, under `bash -o pipefail` so a failed `kubectl` is
not swallowed by the pipe.

**What is left:** nothing of this ticket's own. Dev cases remain
uninvestigable for a different reason — retention, ticket 00.

**Revised 2026-09-18:** not a local kubeconfig — `ssh dev` then `kubectl` on
the host (spec, "Dev is behind SSH"). The Source composes the whole command
in code, greps on the host, and reads `--previous` when the pod is younger
than the window. Measured round trip ~1.5 s.

## What

- One tool module, two functions, both reads: list pods matching the
  channel's `pod_pattern` in the namespace the table names; read logs from
  every match with `--since` covering the window and `--limit-bytes`.
- Same search order and same not-found path as ticket 02; the two branches
  share the code that decides "found", so a fix to one is a fix to both.
- The kubeconfig path and context are `config.yaml` knobs; the operator's
  own file is the default.

## Verify

- Tests with a fake `kubectl` on `PATH`.
- `tests/test_tools.py`'s asserted list grows by exactly these names.

## Owed by the slice (ticket 00, 2026-09-20)

- **The error-code histogram**, as ticket 02 owes it for Loki: the slice caps
  other requests' loud lines at 20 and counts the rest instead.
- **`SshKubectlSource` exists and has never run against the host.** Two round
  trips per read (pod pattern → pod name, then logs), `--since-time` anchored
  to the reporter's message, `-o BatchMode=yes`. This ticket owns proving it
  against the real `ssh dev`, and the `command=` restriction in the host's
  `authorized_keys` the spec calls the second lock.


## Measured on the real host, 2026-09-21 (ticket 00's first run)

- **A dev pod's history is its last restart, and that is short.** Two probes
  about an hour apart saw oldest lines of `2026-09-20T20:25Z` and
  `2026-09-21T07:11:51Z` — a restart in between. A request from
  `2026-09-20T04:41` was unreachable by either.
- **`--tail` counts from the newest line, and there is no `--until`.** A
  window opened 16 hours ago returns the newest N lines of today unless the
  caller clips. `--timestamps` is what makes clipping possible, and the
  runtime's own stamp is the right one: the line's `"time"` field exists on
  this service and not on the next.
- **Round trip, measured again:** pod lookup plus logs is ~2.5 s, so a read
  with one widening is ~5 s. Consistent with ticket 16's 1.5 s per hop.
- `kubectl` writes one warning of its own ahead of the log (`Defaulted
  container …`), which has no stamp. It is kept rather than dropped —
  discarding unparseable lines silently is how a format change becomes an
  empty dossier nobody can explain.

**What this ticket now has to answer:** whether reading dev logs is worth
building out at all, given that a report usually arrives after the pod that
served it has restarted. That is ticket 16's measurement 2, and ticket 00's
open question about replay.


## The histogram, 2026-09-21

Shared with ticket 02 — it is a rule over lines, so it does not know which
back end produced them. See that ticket for what was built and why.

## Re-scoped by architecture v3.3 (2026-09-22)

The operator's call: `Gather` gathers **metadata**, and `Diagnose` reads for
itself through tools. The dev branch becomes the same `read_log` tool; which back end
answers is still the placement's business, not the model's.

Nothing here is thrown away — the reading, the cutting and the guards are
what the tool is made of. What changes is **who decides what to look for**,
and nothing about what is called. See the spec's "Architecture v3.3" and
ticket 15.
