# 06: Approve in Discord and reply as you

**What to build:** A task whose work is finished asks for approval in chat; approving it
posts the reply publicly under the watched account's own identity. This is the first
ticket that writes to a channel anyone else can see.

**Blocked by:** 05, 12

**Status:** ready-for-agent

- [ ] A task reaching the review state sends a direct message containing the proposed reply text and approve/reject controls
- [ ] Approving posts that reply into the originating conversation, appearing as the watched account rather than as an application
- [ ] Rejecting returns the task for human input instead of posting
- [ ] The approval card is itself an outbound row, so a card that fails to send is visible rather than leaving the task waiting for a decision nobody was asked for
- [ ] The reply action refuses to run when the task has not been approved, regardless of which node calls it
- [ ] That refusal is returned to the caller as an ordinary result the model can respond to, not raised as an error
- [ ] Who approved and when is recorded against the task
- [ ] The approval prompt reaches the human without requiring the application identity to be present in the watched channels
