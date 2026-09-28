Status: ready-for-agent
Blocked by: 18

# Doc sweep

## Goal

Each ticket corrected its own docs; this checks nothing drifted.

- `docs/DESIGN.md` § What exists / Layout / Reasoning match the tree.
- `CONTEXT.md` § Vocabulary has every term the map listed (*domain*,
  *action*, *toolset*, *step type*, *model tier*, *enricher*, *spine*, *pass*,
  *continuation point*, *hand-back*, *re-triage*, *retriage note*, *turn*,
  *attempt*, *install fact*, *workspace*, *read-command allowlist*, *agent
  spec*, *toolset spec*, *run context*); § Project state says what runs.
- `.claude/rules/development-rules.md` has the 200-line soft target and the
  `sdk/` rule.
- `domains-plug-in/map.md` marked built; `harness-auto-compaction` unparked
  (its seam re-checked against the split `harness.py`).

## Acceptance

- [ ] Every item above checked, with the diff.
- [ ] Whole suite green.
