Type: grilling
Status: open
Blocked by:

# Where the core's behaviour knobs live

## Question

`config.yaml` now holds only provider keys and named model tiers. Today it also
holds behaviour knobs: triage's `confidence_threshold`, `max_message_age`,
`examples` count, `workflows.auto_ask_for_details` (goes with the extractor), and
per-agent timeouts. Decide where each goes — a code default, a board setting the
operator changes at run time, or a narrow `config.yaml` section kept on purpose —
and which simply disappear.
