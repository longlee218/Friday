"""Compatibility shim: the workflow state moved to `friday.sdk.workflow_state`
when the hand-written engine was retired onto DBOS (ticket 06). Re-exported
here so existing `from friday.dag.state import ...` imports keep working.
"""

from friday.sdk.workflow_state import UNSTORABLE, DAGState, MissingNodeResult

__all__ = ["DAGState", "MissingNodeResult", "UNSTORABLE"]
