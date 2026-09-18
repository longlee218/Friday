"""Ticket 16's model probe. Three things the api_issue design asks of the
configured model, measured through the repo's own Harness, never the SDK.

(a) pick one existing ref among 30 given a dossier            — 20 trials
(b) quote one log line verbatim from a ~2,000-token tool output — 20 trials
(c) finish a three-tool loop through the answer tool          — 10 trials
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import sys
import time
import uuid
from dataclasses import dataclass, field

sys.path.insert(0, "/Users/longlh/Documents/Longle/friday-agents")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/Users/longlh/Documents/Longle/friday-agents/.env")

from friday.agent.harness import Harness, ToolContext, tool  # noqa: E402
from friday.config import load_config  # noqa: E402
from friday.domain.models import FridayState  # noqa: E402

CFG = load_config("/Users/longlh/Documents/Longle/friday-agents/config.yaml")
BASE = CFG.agents["extractor"]
random.seed(7)

CODES = ["ERR19", "ERR303", "ERR306", "ERR943", "ERR951", "ERR955", "ERR24"]
PATHS = ["/v1/midas/intent", "/v1/pod/previews", "/v2/templates/x", "/v1/auth/refresh", "/v1/studio/sub-usecases/a/templates"]
FILES = ["src/modules/midas/midas-payment.service.ts", "src/modules/pod/preview/use-cases/trigger-pod-preview.use-case.ts",
         "src/modules/template/use-cases/get-template-v2.use-case.ts", "src/core/auth/guards/refresh-token.guard.ts"]


def log_line(cid: str, ts: str, code: str, path: str, status: int, user: str) -> str:
    return json.dumps({"level": "ERROR", "time": ts, "context": "ExceptionFilter", "correlationId": cid,
                       "trace": f"ApiException: {code} at {random.choice(FILES)}:{random.randint(20, 120)}",
                       "msg": json.dumps({"method": "POST", "url": path, "statusCode": status, "userId": user,
                                          "error": {"message": "x", "errorCode": code}})}, separators=(",", ":"))


# ---------------------------------------------------------------- (a) ref
@dataclass(frozen=True, slots=True)
class PickRef:
    """Which single piece of evidence shows where the error was raised."""
    ref: str = field(default="", metadata={"doc": "one ref, copied exactly from the dossier"})
    why: str = field(default="", metadata={"doc": "one sentence"})


async def probe_a(n: int) -> tuple[int, int]:
    grounded = right = 0
    h = Harness(config=BASE, instructions="You diagnose API faults from a dossier. Answer only through the tool; copy refs exactly.", answers=PickRef, context_type=FridayState)
    for i in range(n):
        refs = []
        target_code = random.choice(CODES)
        target = None
        for k in range(30):
            cid = str(uuid.uuid4())
            kind = random.choice(["loki", "file"])
            if kind == "loki":
                ref = f"loki:{cid[:8]}:{random.randint(10, 23):02d}:{random.randint(0, 59):02d}"
                code = random.choice(CODES)
                claim = f"{code} on {random.choice(PATHS)} for user u{random.randint(1, 9)}"
            else:
                ref = f"file:{random.choice(FILES)}:{random.randint(20, 140)}"
                claim = f"throws {random.choice(CODES)} when the guard fails"
            refs.append((ref, claim))
        # plant the decisive one
        t_idx = random.randrange(30)
        target = f"file:{random.choice(FILES)}:{random.randint(20, 140)}"
        refs[t_idx] = (target, f"the line that raises {target_code} (the reporter's error)")
        dossier = "\n".join(f"- {c} [ref {r}]" for r, c in refs)
        prompt = f"The reporter got {target_code}. Dossier:\n{dossier}\n\nWhich ref shows the line where {target_code} is raised?"
        out = await h.run_structured(prompt, context=FridayState(channel_id="probe", agent="probe"))
        ok_ground = out is not None and any(out.ref == r for r, _ in refs)
        ok_right = out is not None and out.ref == target
        grounded += ok_ground; right += ok_right
        print(f"a{i:02d} grounded={ok_ground} right={ok_right} ref={getattr(out, 'ref', None)!r} err={h.last_error}")
    return grounded, right


# ---------------------------------------------------------------- (b) quote
@dataclass(frozen=True, slots=True)
class Quote:
    """One log line, copied verbatim."""
    quote: str = field(default="", metadata={"doc": "the whole line, byte for byte, no paraphrase"})


async def probe_b(n: int) -> int:
    ok = 0
    h = Harness(config=BASE, instructions="You copy evidence verbatim. Never paraphrase or reformat a quoted line.", answers=Quote, context_type=FridayState)
    for i in range(n):
        lines = []
        target_cid = None
        for k in range(28):  # ~28 lines × ~290 chars ≈ 8k chars ≈ 2k tokens
            cid = str(uuid.uuid4())
            lines.append(log_line(cid, f"2026-09-17T23:{random.randint(0,59):02d}:{random.randint(0,59):02d}Z", random.choice(CODES), random.choice(PATHS), random.choice([400, 404, 429, 500]), f"u{random.randint(1,9)}"))
            if k == random.randrange(28) or target_cid is None:
                target_cid = cid
        blob = "\n".join(lines)
        prompt = f"Tool output (search_logs):\n{blob}\n\nQuote, verbatim, the one line whose correlationId is {target_cid}."
        out = await h.run_structured(prompt, context=FridayState(channel_id="probe", agent="probe"))
        good = out is not None and out.quote.strip() in blob and target_cid in out.quote
        ok += good
        print(f"b{i:02d} verbatim={good} len={len(getattr(out, 'quote', '') or '')} err={h.last_error}")
    return ok


# ---------------------------------------------------------------- (c) loop
@dataclass(frozen=True, slots=True)
class Verdict:
    """The cause, with the refs that support it."""
    cause: str = field(default="", metadata={"doc": "one sentence"})
    refs: list[str] = field(default_factory=list, metadata={"doc": "refs returned by the tools you called"})


def loop_tools(calls: list[str]):
    async def search_logs(ctx: ToolContext[FridayState], search: str) -> str:
        """Search the service's logs for a substring.

        Args:
            search: the substring to look for, e.g. a correlationId or error code.
        """
        calls.append("search_logs")
        return "found 1 line [ref loki:aa11:23:20]: ERR303 Don't have any transaction on /v1/midas/intent for user u3"

    async def read_source(ctx: ToolContext[FridayState], path: str, line: int) -> str:
        """Read the source around one line.

        Args:
            path: file path from a stack frame.
            line: the line number.
        """
        calls.append("read_source")
        return "[ref file:src/modules/midas/midas-payment.service.ts:86] if (!tx) throw new ApiException('Don't have any transaction', 'ERR303')"

    async def db_lookup(ctx: ToolContext[FridayState], check: str, key_value: str) -> str:
        """Run a declared database check by key.

        Args:
            check: the declared check name, e.g. transactions_by_user.
            key_value: the key, e.g. a userId.
        """
        calls.append("db_lookup")
        return "[ref db:transactions:u3] 0 rows — user u3 has no transaction"

    return [tool(search_logs), tool(read_source), tool(db_lookup)]


async def probe_c(n: int) -> tuple[int, int]:
    finished = all_three = 0
    for i in range(n):
        calls: list[str] = []
        cfg = dataclasses.replace(BASE, max_turns=8)
        h = Harness(config=cfg, instructions=("You diagnose an API fault. First call search_logs with the error code, then read_source on the frame it names, "
                                              "then db_lookup with check 'transactions_by_user' and the user, then answer through the answer tool with the refs you saw."),
                    tools=loop_tools(calls), answers=Verdict, context_type=FridayState)
        out = await h.run_structured("Reporter: POST /v1/midas/intent returns 400 ERR303 for user u3. Find the cause.",
                                     context=FridayState(channel_id="probe", agent="probe"))
        fin = out is not None and len(out.refs) >= 1
        three = {"search_logs", "read_source", "db_lookup"} <= set(calls)
        finished += fin; all_three += three
        print(f"c{i:02d} finished={fin} all_three={three} calls={calls} refs={getattr(out, 'refs', None)} err={h.last_error}")
    return finished, all_three


async def main() -> None:
    t0 = time.time()
    ga, ra = await probe_a(20)
    gb = await probe_b(20)
    fc, tc = await probe_c(10)
    print("\n=== RESULTS ===")
    print(f"(a) pick-ref: grounded {ga}/20 = {ga*5}%   right {ra}/20 = {ra*5}%")
    print(f"(b) verbatim quote: {gb}/20 = {gb*5}%")
    print(f"(c) tool loop: finished {fc}/10 = {fc*10}%   called all three {tc}/10 = {tc*10}%")
    print(f"model={BASE.model} elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
