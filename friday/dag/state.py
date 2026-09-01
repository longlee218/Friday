"""What a DAG has learned so far.

One value per node, keyed by node name. Immutable: every write returns a new
state, so a node cannot reach back and change what an earlier one recorded.
That is what makes the checkpoint honest — the saved state is exactly what the
next node will read.

Reading a key no node has written raises rather than returning None. A node
that reads `state["analyze"]` before `analyze` ran is a wiring mistake in the
edges, and it should say so at the point of the mistake rather than silently
handing over a None that turns into a bad prompt three nodes later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DAGState", "MissingNodeResult", "UNSTORABLE"]

#: Marks a node that ran but whose result does not survive a round trip
#: through JSON. See `DAGState.to_dict`.
UNSTORABLE = "__unstorable__"


def _is_marker(value: Any) -> bool:
    return isinstance(value, dict) and UNSTORABLE in value


class MissingNodeResult(KeyError):
    """A node read a result no node has produced.

    Raised rather than returning None: the caller wrote an edge that does not
    guarantee the ordering it assumed, and the fix is in the edges.
    """


@dataclass(frozen=True, slots=True)
class DAGState:
    """Node name -> whatever that node returned."""

    results: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "DAGState":
        return cls(results={})

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "DAGState":
        """Rebuild from storage. A missing or malformed row starts empty.

        **Nodes marked unstorable are dropped, so they run again.** The two
        directions are deliberately asymmetric: the stored row keeps the
        marker so someone reading the table can see the node ran and what it
        produced, and the rebuilt state does not, because a node whose value
        we no longer have has not usefully completed. Skipping it would leave
        a hole every downstream node reads as absence.

        The cost is that such a node repeats on resume. The fix, where that
        matters, is to split it: an expensive node returning plain data, and a
        cheap one turning that data into whatever object the caller wanted.

        Tolerant otherwise: a state written by an older version of a graph
        should let the task make progress, not wedge it. The runner walks
        forward from the entry, so an unrecognised key is simply never read.
        """
        if not isinstance(raw, dict):
            return cls.empty()
        return cls(
            results={
                name: value
                for name, value in raw.items()
                if not _is_marker(value)
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe projection, for storage.

        The live state holds whatever a node returned, including domain
        objects like `Reply`. Storage cannot, and the two needs are different
        enough to keep apart: the in-memory value is what the next node reads
        *this* run; the stored value is only ever read to decide whether a
        node still needs to run.

        A value that will not serialise is replaced by a marker naming its
        type. The node still counts as complete — that is the point, since
        re-running it is what the checkpoint exists to avoid — and a
        downstream node that needed the real value gets a shape it visibly
        did not expect rather than a silent `None`.
        """
        safe: dict[str, Any] = {}
        for name, value in self.results.items():
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                safe[name] = {UNSTORABLE: type(value).__name__}
            else:
                safe[name] = value
        return safe

    def has(self, node: str) -> bool:
        return node in self.results

    def get(self, node: str, default: Any = None) -> Any:
        """The result, or `default`. Use when absence is expected."""
        return self.results.get(node, default)

    def with_result(self, node: str, result: Any) -> "DAGState":
        """A new state with one more node recorded."""
        return DAGState(results={**self.results, node: result})

    def __contains__(self, node: object) -> bool:
        return node in self.results

    def __getitem__(self, node: str) -> Any:
        try:
            return self.results[node]
        except KeyError:
            raise MissingNodeResult(
                f"no result recorded for {node!r}; recorded so far: "
                f"{sorted(self.results)}"
            ) from None
