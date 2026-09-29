"""Evals: registered sets of cases, run on Pydantic Evals by the core.

A plugin (or the core) registers an `EvalSpec` (`api.eval`); `run.py` turns
it into a Pydantic Evals `Dataset` and runs it against a task the composition
root builds (`run_eval.py`). `run.py` is the one module that imports
`pydantic_evals`. `cases.py` reads the markdown case format under
`evals/datasets/`; `triage.py` is the core's own eval, `core.triage`.

Not run by the suite: an eval calls the configured provider, so it costs
money and is read by a person. What the suite checks is the wiring and the
arithmetic, on scripted models.
"""
