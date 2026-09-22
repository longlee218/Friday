# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those
roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

The five above are triage *roles* — readiness before work. This tracker adds
one completion state, since a finished ticket needs somewhere to say so:

| Label                      | Meaning                                                  |
| -------------------------- | -------------------------------------------------------- |
| `done`                     | Acceptance boxes ticked and the work committed (`/implement` sets this on close-out) |

Defaults kept as-is: nothing in this repo used a competing vocabulary.

Edit the right-hand column to match whatever vocabulary you actually use.
