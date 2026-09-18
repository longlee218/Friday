"""(b') the model POINTS at a line inside ~2,000 tokens of raw tool output; code copies it."""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).parent))
import asyncio, json, random, uuid
from dataclasses import dataclass, field
from probe_model import BASE, CODES, PATHS, log_line, Harness, FridayState  # reuses config + generators

@dataclass(frozen=True, slots=True)
class Pointer:
    """Which line: its correlationId, copied exactly."""
    correlation_id: str = field(default="", metadata={"doc": "the correlationId of the one matching line, copied exactly"})

async def main():
    random.seed(11); ok = 0
    h = Harness(config=BASE, instructions="You locate evidence in tool output and point at it by id. Answer only through the tool.", answers=Pointer, context_type=FridayState)
    for i in range(20):
        lines, meta = [], []
        for k in range(28):
            cid = str(uuid.uuid4()); code = random.choice(CODES); path = random.choice(PATHS); user = f"u{random.randint(1,9)}"
            lines.append(log_line(cid, f"2026-09-17T23:{random.randint(0,59):02d}:{random.randint(0,59):02d}Z", code, path, 400, user)); meta.append((cid, code, path, user))
        t = random.randrange(28); cid, code, path, user = meta[t]
        # make the target unique on (code, path, user)
        for j,(c2,co,pa,us) in enumerate(meta):
            if j!=t and (co,pa,us)==(code,path,user): lines[j]=lines[j].replace(us, "u0")
        blob = "\n".join(lines)
        prompt = f"Tool output (search_logs):\n{blob}\n\nWhich line is the {code} on {path} for user {user}? Give its correlationId."
        out = await h.run_structured(prompt, context=FridayState(channel_id="probe", agent="probe"))
        good = out is not None and out.correlation_id.strip() == cid
        ok += good
        print(f"b'{i:02d} right={good} got={getattr(out,'correlation_id',None)!r} err={h.last_error}")
    print(f"\n(b') point-at-line: {ok}/20 = {ok*5}%")
if __name__ == "__main__":
    asyncio.run(main())
