"""Reading a product's database — the guards that are code, not a prompt.

Board `read-it-the-way-the-operator-does`. The operator's call of 2026-09-21
reversed ticket 15's "no SQL at any layer": which table answers a question is
reasoning, and a table of pre-declared checks cannot enumerate it in advance.
The argument for the restriction was about writes, and every database this
server offers answers `"permission": "reader"`.

So what is tested here is what a *read* can still get wrong.
"""

from __future__ import annotations

import json

import pytest

from plugins.backend.sources.db import DbSource, redacted_name

#: The shape measured against the live server on 2026-09-21: an object whose
#: `result` is a JSON *string*, and inside it `{rows, count}`.
def answered(inner) -> dict:
    return {"result": json.dumps(inner)}


class FakeServer:
    """Records what it was asked, answers from a script."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[tuple[str, dict]] = []

    async def call(self, tool, arguments):
        self.asked.append((tool, arguments))
        return self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]


def source(*answers, allowed=("reelme",), **kwargs) -> DbSource:
    return DbSource(
        server=FakeServer(*answers), allowed=frozenset(allowed), **kwargs
    )


# --- which database ---------------------------------------------------------


async def test_a_room_reads_only_the_databases_it_was_given():
    """Eleven exist across two ventures. Which ones this room may read is a
    row somebody wrote, and the board's rule is that a missing row is a
    hand-over rather than a guess."""
    db = source(answered({"rows": []}))

    with pytest.raises(PermissionError, match="supermind-postgres-backend-reelme"):
        await db.query("supermind-postgres-backend-reelme", "select 1")


async def test_a_room_with_no_databases_written_down_reads_none():
    db = source(answered({"rows": []}), allowed=())

    with pytest.raises(PermissionError, match="none are written down"):
        await db.query("anything", "select 1")


# --- what kind of statement -------------------------------------------------


async def test_only_a_read_is_sent():
    """The credential is what actually stops a write. This stops a model that
    has misunderstood what it was asked to do, which is a different failure
    and a louder one."""
    db = source(answered({"rows": []}))

    with pytest.raises(PermissionError, match="does not start a read"):
        await db.query("reelme", "delete from \"Purchase\" where id = '1'")


async def test_a_common_table_expression_is_a_read():
    db = source(answered({"rows": [{"n": 1}], "count": 1}))

    found = await db.query("reelme", 'WITH x AS (SELECT 1 AS n) SELECT * FROM x')

    assert found.rows == ({"n": 1},)


async def test_a_comment_is_not_mistaken_for_a_second_statement():
    """What stripping comments actually buys, which is not safety: a
    statement that does not begin with a read is already refused, comment or
    no comment. What it prevents is refusing a *legitimate* read whose
    comment happens to hold a `;` — a false refusal nobody could explain from
    the message."""
    db = source(answered({"rows": [{"n": 1}], "count": 1}))

    found = await db.query("reelme", "select 1 as n -- careful; this one is fine")

    assert found.rows == ({"n": 1},)
    assert ";" not in db.server.asked[-1][1]["sql"]


async def test_a_statement_that_hides_behind_a_comment_is_still_not_a_read():
    """Refused either way — the comment is stripped, and an unrecognised
    beginning is refused. Both roads lead to no, which is the direction an
    unclear case should fail in."""
    db = source(answered({"rows": []}))

    with pytest.raises(PermissionError, match="does not start a read"):
        await db.query("reelme", "/* select */ drop table \"Purchase\"")


async def test_two_statements_are_refused_rather_than_half_run():
    db = source(answered({"rows": []}))

    with pytest.raises(PermissionError, match="one statement at a time"):
        await db.query("reelme", "select 1; drop table \"Purchase\"")


async def test_a_trailing_semicolon_is_not_two_statements():
    db = source(answered({"rows": [{"n": 1}], "count": 1}))

    found = await db.query("reelme", "select 1 as n;")

    assert found.rows == ({"n": 1},)
    assert db.server.asked[-1][1]["sql"] == "select 1 as n"


# --- how much comes back ----------------------------------------------------


async def test_the_answer_is_capped_and_says_so():
    """Measured 2026-09-21: this server does **not** honour a `LIMIT` —
    `SELECT * FROM (three rows) LIMIT 2` came back with three. So the cap is
    applied to what arrives, which protects the prompt and not the database:
    a heavy query is still heavy on the far side."""
    db = source(
        answered({"rows": [{"n": i} for i in range(10)], "count": 10}),
        max_rows=3,
    )

    found = await db.query("reelme", "select n from generate_series(1,10) n")

    assert len(found.rows) == 3
    assert found.truncated is True
    assert found.count == 10, "what the database returned, not what survived"


# --- who is in the rows -----------------------------------------------------


def test_the_columns_that_carry_people_are_known_by_name():
    """Stems rather than a list of every column: a list is a snapshot of one
    schema on one day, and the next migration adds a column nobody updates it
    for."""
    for column in (
        "email", "receiptEmail", "ipAddress", "purchaseToken",
        "appAccountToken", "rawPayload", "decodedPayload", "userAgent",
        "shippingAddress", "idfa", "gpsAdid", "headers",
    ):
        assert redacted_name(column), column

    for column in (
        "id", "userId", "status", "transactionId", "createdAt", "amount",
        "description", "retryCount", "eventType",
    ):
        assert not redacted_name(column), column


async def test_a_value_that_is_a_person_never_leaves_the_database():
    """`scrub` catches a credential and not an address. A model free to query
    a payment database pulls real people into a prompt and into a report file
    on disk."""
    db = source(answered({
        "rows": [{
            "userId": "u-1", "status": "FAILED",
            "email": "someone@real.example", "ipAddress": "1.2.3.4",
        }],
        "count": 1,
    }))

    found = await db.query("reelme", 'select * from "PSPLedgerTransaction"')

    (row,) = found.rows
    assert row["email"] == "[REDACTED]"
    assert row["ipAddress"] == "[REDACTED]"
    assert (row["userId"], row["status"]) == ("u-1", "FAILED")
    assert found.redacted == ("email", "ipAddress")
    assert "real.example" not in found.text()


async def test_the_column_survives_even_though_its_value_does_not():
    """A diagnosis can say a receipt email was recorded without carrying it.
    A column silently dropped is a column the model concludes was never
    there."""
    db = source(answered({"rows": [{"receiptEmail": "x@y.z"}], "count": 1}))

    (row,) = (await db.query("reelme", "select 1")).rows

    assert list(row) == ["receiptEmail"]


async def test_a_null_in_a_personal_column_stays_null():
    """`[REDACTED]` where the database held nothing would invent a fact."""
    db = source(answered({"rows": [{"email": None}], "count": 1}))

    (row,) = (await db.query("reelme", "select 1")).rows

    assert row["email"] is None


# --- the schema, one table at a time ----------------------------------------


async def test_a_schema_can_be_asked_for_one_table():
    """Measured: the payment database answers with fifteen tables including
    `pg_stat_statements` and its forty columns, which is not a thing to put
    in a prompt to find out where `userId` lives."""
    db = source(answered({
        "Purchase": [{"column": "userId", "type": "text"}],
        "pg_stat_statements": [{"column": "queryid", "type": "bigint"}],
    }))

    found = await db.schema("reelme", table="Purchase")

    assert list(found) == ["Purchase"]


async def test_an_answer_the_server_never_gave_is_empty_rather_than_a_crash():
    db = source({"result": "not json"})

    assert (await db.query("reelme", "select 1")).rows == ()


# --- what it may call -------------------------------------------------------


def test_it_declares_the_tools_it_calls_and_not_the_one_it_does_not():
    """`execute_mongo_query` is read-only by construction and would be the
    obvious fourth. Nothing reads Mongo yet, and a tool declared ahead of a
    caller is a tool nobody can say the shape of."""
    assert DbSource.TOOLS == {
        "list_databases", "describe_schema", "execute_query",
    }
