# 03: Dev logs through SSH to the dev host

**What to build:** A `friday/tools/` read tool wrapping `kubectl get pods`
and `kubectl logs`, and the dev branch of the log node.

**Blocked by:** 01.

**Decisions:** D4, D5, D14.

**Status:** ready-for-agent

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
