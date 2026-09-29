"""`backend.db`: reading a product's database, when a log line is not enough.

The client (`DbSource`) and its two tools, `describe_db` and `query_db`
(build-the-spine ticket 09). Registered, granted to no action yet: no
contract names `backend.db` today, and the room's databases
(`Placement.dbs`) are not filled by the enricher. Over the 200-line soft
target because the client and its tools are one file by decision
(`domains-plug-in` ticket 09 §3).

**The model decides which table answers the question.** That is the
operator's call of 2026-09-21 and it reverses ticket 15's "no SQL at any
layer": which table matters is reasoning, not something a table of
pre-declared checks can enumerate in advance. The spec's own split already
allowed for it — "the difference is **who decides what to look for**, never
what is called" — and the database was the one source where both halves were
pre-declared, on an argument about writes.

That argument does not hold: every database this server offers answers
`"permission": "reader"`, so a statement that is not a read fails at the
database rather than at our intentions. What is left once the fear of writes
goes is **four things a read can still get wrong**, and each one is a rule
here rather than a line in a prompt:

- **A wrong query answers confidently.** `UsageTransaction` is the best-named
  table in the payment schema and carries no `userId` at all; a model that
  picks it gets nothing back and concludes there was no transaction. Nothing
  here can prevent that. What it can do is refuse a statement that is not a
  read, so a model that has misunderstood its job says so loudly.
- **A room may only read its own databases.** Eleven exist across two
  ventures. `allowed` is the set some row said this room may see, and an
  empty set reads nothing — the board's rule that a missing row is a
  hand-over rather than a guess.
- **The answer is capped**, and the cap says so. Measured 2026-09-21: this
  server **does not honour a `LIMIT`** — `SELECT * FROM (three rows) LIMIT 2`
  came back with three. So the cap is applied to the rows after they arrive,
  which protects the prompt and **not** the database: a heavy query is still
  heavy on the far side, and nothing here can make it lighter.
- **Personal data is removed by column name.** The payment schema carries
  `email`, `receiptEmail`, `ipAddress`, `purchaseToken`, `appAccountToken`
  and jsonb receipts. `scrub` catches credentials and not an address; a
  model free to query a payment database pulls real people into a prompt and
  into a report file on disk. The column's *name* survives, so a diagnosis
  can say a receipt email was recorded without carrying it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from friday.sdk.toolset import RunContext, ToolsetSpec, tool
from plugins.backend.placement import Placement
from plugins.backend.toolsets.evidence import Evidence

__all__ = ["DB", "DB_SERVER", "DbSource", "Rows", "db_tools", "redacted_name"]

log = logging.getLogger(__name__)

#: The MCP server the databases are read through.
DB_SERVER = "db-generic"

#: What the answer is trimmed to before anyone reads it. The far side has
#: already done the work — see the module docstring — so this is the prompt's
#: budget, not the database's.
MAX_ROWS = 50

#: Column names whose value never leaves the database. Matched on the
#: lower-cased name: `stems` anywhere in it, `names` exactly.
#:
#: Stems rather than a list of every column, because the list would be a
#: snapshot of one schema on one day and the next migration adds a column
#: nobody updates it for. Short ones are exact for the same reason a stem is
#: not: `ip` inside `description` is not an address.
_PII_STEMS = (
    "email",
    "token",
    "secret",
    "password",
    "passwd",
    "phone",
    "address",
    "payload",
    "useragent",
)
_PII_NAMES = frozenset(
    {
        "ip",
        "adid",
        "idfa",
        "idfv",
        "gpsadid",
        "androidid",
        "headers",
        "devicemetadata",
        "obfuscatedexternalaccountid",
    }
)

#: A read. `with` is here because a CTE is one, and this is the whole of the
#: check: the credential is what actually stops a write, and this only stops
#: a model that has misunderstood what it was asked to do.
_READS = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

#: `--` to end of line, and `/* … */`. Stripped before the check above, so a
#: statement cannot hide behind a comment.
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def redacted_name(column: str) -> bool:
    """Whether this column's values are personal data."""
    lowered = column.lower()
    return lowered in _PII_NAMES or any(stem in lowered for stem in _PII_STEMS)


@dataclass(frozen=True, slots=True)
class Rows:
    """What a query answered, and what was taken out of it."""

    rows: tuple[dict[str, Any], ...] = ()
    #: How many the database returned, before the cap.
    count: int = 0
    truncated: bool = False
    #: Columns whose values were removed. Named so a diagnosis can say a
    #: receipt email was recorded without carrying it.
    redacted: tuple[str, ...] = ()

    def text(self) -> str:
        return json.dumps(
            {"rows": list(self.rows), "count": self.count},
            ensure_ascii=False,
            indent=1,
            default=str,
        )


@dataclass(frozen=True, slots=True)
class DbSource:
    """The database MCP, narrowed to reads of the rooms's own databases.

    `execute_mongo_query` is not declared. It is read-only by construction
    ("MVP: find only") and would be the obvious fourth, and nothing reads
    Mongo yet — a tool declared ahead of a caller is a tool nobody can say
    the shape of.
    """

    TOOLS = frozenset({"list_databases", "describe_schema", "execute_query"})

    server: Any
    #: The `db_id`s this room may read. Empty reads nothing.
    allowed: frozenset[str] = field(default_factory=frozenset)
    name: str = "db"
    max_rows: int = MAX_ROWS

    async def databases(self) -> list[dict[str, Any]]:
        """Every database the credential can see, with its engine and its
        permission. Unfiltered on purpose: this is what the server is willing
        to say about itself, and it is how an operator finds the `db_id` to
        put in a row."""
        return _json(await self.server.call("list_databases", {})) or []

    async def schema(self, db_id: str, table: str | None = None) -> dict[str, Any]:
        """A database's tables and columns, or one table's.

        `table` is not an argument the server takes — it filters here.
        Measured 2026-09-21: the payment database answers with fifteen tables
        including `pg_stat_statements` and its forty columns, which is not a
        thing to put in a prompt to find out where `userId` lives.
        """
        self._permitted(db_id)
        found = _json(await self.server.call("describe_schema", {"db_id": db_id}))
        if not isinstance(found, dict):
            return {}
        if table is None:
            return found
        return {name: columns for name, columns in found.items() if name == table}

    async def query(self, db_id: str, sql: str) -> Rows:
        """Run a read the model composed, and hand back what is safe to read.

        The statement is the model's. The database, the cap and what comes
        back out of it are not.
        """
        self._permitted(db_id)
        bare = _COMMENTS.sub(" ", sql).strip().rstrip(";")
        if ";" in bare:
            raise PermissionError(
                "one statement at a time: a `;` in the middle of this one "
                "means two, and only the first would be read"
            )
        if not _READS.match(bare):
            raise PermissionError(
                f"this reads, and {bare.split()[0] if bare.split() else 'that'!r} "
                "does not start a read. Every database here is a reader, so "
                "the question is what you meant, not what you could do."
            )
        answer = _json(
            await self.server.call("execute_query", {"db_id": db_id, "sql": bare})
        )
        return _capped(answer, self.max_rows)

    def _permitted(self, db_id: str) -> None:
        if db_id not in self.allowed:
            raise PermissionError(
                f"{db_id!r} is not one of this room's databases "
                f"({sorted(self.allowed) or 'none are written down'})"
            )


def _capped(answer: Any, max_rows: int) -> Rows:
    """The server's answer, trimmed and with personal data taken out."""
    if not isinstance(answer, dict):
        return Rows()
    raw = answer.get("rows")
    if not isinstance(raw, list):
        return Rows(count=int(answer.get("count", 0) or 0))

    removed: set[str] = set()
    kept: list[dict[str, Any]] = []
    for row in raw[:max_rows]:
        if not isinstance(row, dict):
            continue
        clean: dict[str, Any] = {}
        for column, value in row.items():
            if value is not None and redacted_name(str(column)):
                removed.add(str(column))
                clean[column] = "[REDACTED]"
            else:
                clean[column] = value
        kept.append(clean)
    if removed:
        log.info("db: removed %s from what was read", sorted(removed))
    return Rows(
        rows=tuple(kept),
        count=int(answer.get("count", len(raw)) or len(raw)),
        truncated=len(raw) > max_rows,
        redacted=tuple(sorted(removed)),
    )


def _json(result: Any) -> Any:
    """This server answers `{"result": "<json>"}` — a string inside an
    object. Unreadable is empty rather than an exception, for the reason the
    log sources give: a changed shape should read as "it said nothing I
    understood", which a node reports, and not as a crashed graph."""
    said = getattr(result, "content", result)
    if isinstance(said, list):
        said = "".join(
            part if isinstance(part, str) else str(getattr(part, "text", ""))
            for part in said
        )
    try:
        outer = json.loads(said) if isinstance(said, str) else said
    except (TypeError, ValueError):
        log.warning("the db server answered something that is not JSON")
        return None
    inner = outer.get("result") if isinstance(outer, dict) else outer
    if isinstance(inner, str):
        try:
            return json.loads(inner)
        except ValueError:
            log.warning("the db server's `result` is not JSON")
            return None
    return inner


def _describe_db(evidence: Evidence, source: DbSource | None):
    @tool
    async def describe_db(db_id: str, table: str = "") -> str:
        """The tables and columns of one of this room's databases, or of one
        table. Look before you query: the best-named table is not always the
        one that holds the column.

        Args:
            db_id: the database, as this room records it.
            table: one table's name, or empty for every table.
        """
        if spent := evidence.spent():
            return spent
        if source is None:
            return f"{DB_SERVER} is not connected, so no database can be read."
        evidence.reads += 1
        try:
            found = await source.schema(db_id, table or None)
        except PermissionError as refused:
            return str(refused)
        return json.dumps(found, ensure_ascii=False, indent=1) if found else "Nothing."

    return describe_db


def _query_db(evidence: Evidence, source: DbSource | None):
    @tool
    async def query_db(db_id: str, sql: str) -> str:
        """Run one read (`SELECT` or `WITH`) on one of this room's databases.

        At most 50 rows come back, and personal columns (emails, tokens,
        addresses) come back as `[REDACTED]`. Every row is citable.

        Args:
            db_id: the database, as this room records it.
            sql: one statement that reads.
        """
        if spent := evidence.spent():
            return spent
        if source is None:
            return f"{DB_SERVER} is not connected, so no database can be read."
        evidence.reads += 1
        try:
            rows = await source.query(db_id, sql)
        except PermissionError as refused:
            return str(refused)
        if rows.truncated:
            evidence.not_checked.append(
                f"the query on {db_id} was capped at {source.max_rows} rows of {rows.count}"
            )
        if not rows.rows:
            return f"No rows ({rows.count} counted)."
        return evidence.show(
            json.dumps(row, ensure_ascii=False, default=str) for row in rows.rows
        )

    return query_db


def db_tools(run: RunContext) -> list:
    """`backend.db`'s factory: the room's databases (`Placement.dbs`) only."""
    reads = run.mcp.get(DB_SERVER)
    source = (
        None
        if reads is None
        else DbSource(server=reads, allowed=frozenset(run.domain.dbs))
    )
    return [_describe_db(run.evidence, source), _query_db(run.evidence, source)]


DB = ToolsetSpec(
    name="backend.db",
    description=(
        "Read the room's own databases, one read statement at a time, "
        "personal columns redacted: describe_db, query_db."
    ),
    factory=db_tools,
    mcp={DB_SERVER: DbSource.TOOLS},
    domain_type=Placement,
)
