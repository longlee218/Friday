---
name: harness-retry-vs-pydantic-ai-error-mapping
description: pydantic_ai's OpenAIChatModel wraps openai errors into ModelHTTPError/ModelAPIError, so harness retry tests that raise openai errors from a fake model do not reflect production
metadata:
  type: project
---

Found 2026-09-28 (pydantic-ai-slim 2.46.0): `pydantic_ai.models.openai._map_api_errors` turns `APIStatusError` into `ModelHTTPError` and `APIConnectionError` into `ModelAPIError` (both `AgentRunError`, not openai classes). `friday/kernel/harness/retry.py::_TRANSIENT` lists openai classes, and `tests/test_harness.py` raises them straight from a fake model, which skips the wrapping.

**Why:** in production only `TimeoutError` probably gets retried. Reported to the operator during build-the-spine ticket 03 review, outside that ticket's scope.

**How to apply:** when reviewing harness retry code or a pydantic_ai bump, check whether this was fixed (for example, `_transient` reads `ModelHTTPError.status_code` / `__cause__`) before raising it again.
