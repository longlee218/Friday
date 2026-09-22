# 17: Remaining "now" §12 controls

**What to build:** The security controls DESIGN-v2 §12 marks "now" are in place — the approval card tells the whole truth, the audit log records who did what, secrets are redacted by value, and MCP children get only what they were given.

**Blocked by:** 14.

**Status:** ready-for-agent

- [ ] The approval card shows the exact bytes, destination, audience (public/private), expanded links/mentions/attachments, and flags secret-pattern and secret-value matches; redaction also runs on drafts
- [ ] An application-level append-only audit log records approvals (who approved which bytes), plugin loads with tiers, MCP-grant changes and refused handle requests
- [ ] Value-based redaction of every declared secret
- [ ] MCP children get only the SDK's safe env plus the server's declared secrets, nothing else
- [ ] Each control has a test
- [ ] `uv run pytest -q` passes
