"""Build (or refresh) `evals/triage.jsonl` from live data.

Run by hand, when there is new data worth freezing in — never by
`run_triage_eval.py` and never by the suite (D7): a set that re-read the
database on every scoring run would move under the prompt it is scoring, so
a prompt edit and a change in what the operator has since marked would land
in the same number with no way to tell which moved it.

    uv run python -m evals.build_triage_set
    FRIDAY_DB=/path/to/db uv run python -m evals.build_triage_set

As of this ticket the shipped database has no marked verdicts yet —
`db.confirmed_classifications()` returns nothing until the operator starts
reacting ✅ to real classifications — so today's file is entirely `SEED`
below. Delete a seed row once a real marked verdict says the same thing; it
exists to unblock this ticket, not to be defended forever.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from friday.config import Config, load_config
from friday.store.db import Database

from evals.dataset import build_frozen_set, write_jsonl

__all__ = ["SEED", "build_and_write", "main"]

OUT = Path(__file__).parent / "triage.jsonl"

#: Hand-written, covering the four classifiable types. Not the production
#: few-shot examples — those are `config.yaml`'s `triage_examples`, read
#: separately below and excluded from this set for exactly that reason:
#: scoring a classifier on the sentence it was already told the answer to
#: is not a measurement of anything. Bilingual because real reports are —
#: `CLAUDE.md` cites "token hết hạn rồi" as an ordinary bug report for the
#: same reason.
SEED: list[tuple[str, str]] = [
    ("the checkout api keeps returning 500 on prod, anyone seen this", "api_issue"),
    ("getting a 403 from /v2/orders since this morning, did something change", "api_issue"),
    ("api trả về lỗi 502 liên tục từ tối qua, có ai check giúp không", "api_issue"),
    ("token hết hạn rồi, gọi api báo unauthorized", "api_issue"),
    ("can I get write access to the payments repo", "access_request"),
    ("need read access to the analytics dashboard for the new hire", "access_request"),
    ("xin quyền truy cập vào kho staging cho dự án mới", "access_request"),
    ("could you add me to the #incidents channel", "access_request"),
    ("where's the spec for the refund flow", "doc_question"),
    ("do we have docs on how retries are configured for the outbox", "doc_question"),
    ("tài liệu về luồng duyệt task ở đâu vậy", "doc_question"),
    ("is there a runbook for rotating the discord token", "doc_question"),
    ("anyone want lunch", "skip"),
    ("happy friday everyone", "skip"),
    ("lol nice one", "skip"),
    ("chúc mừng sinh nhật nha", "skip"),
]


async def build_and_write(db: Database, config: Config, *, out: Path = OUT) -> int:
    """Read what a real deploy would show `run_triage_eval.py`'s frozen set,
    write it, and return how many rows landed. Split from `main` so a test
    can supply an in-memory `db` and a throwaway `out` — no real database,
    no touching the committed file."""
    confirmed = await db.confirmed_classifications(limit=1000)
    frozen = build_frozen_set(
        confirmed=confirmed, seed=SEED, excluded=list(config.triage_examples)
    )
    write_jsonl(out, frozen)
    return len(frozen)


async def main() -> None:
    load_dotenv()  # `run_agent.py`'s own first step — secrets from .env, never config.yaml.
    config = load_config()
    db_path = os.environ.get("FRIDAY_DB") or config.database_path
    db = await Database.connect(db_path)
    try:
        count = await build_and_write(db, config)
    finally:
        await db.close()
    print(f"wrote {count} examples to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
