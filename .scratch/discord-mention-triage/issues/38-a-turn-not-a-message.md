# 38: A turn, not a message

**What to build:** The unit of work becomes what somebody said, not each
message they sent it in. Three messages in five seconds are one report, read
once, classified once.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

## Why

Every hole found so far has the same shape: the system counts messages and
people speak in turns.

    @Lee a Long ơi API lỗi rồi a ơi
    chi tiết đây a: curl -X POST … correlationId abcdef01-… trên production
                                                        ← 3 seconds later

Only the first carries a mention. The second was dropped, and the system asked
for a correlationId the reporter had sent three seconds earlier. A reply to our
own question was dropped for the same reason. Each was patched where it hurt;
none of them changed the unit, so the next shape of message will be the next
hole and we will find it the same way — in production, from the operator.

It is also three model calls where one would do.

## When a turn ends

Discord sends a typing signal, repeated about every ten seconds while someone
is writing. So the boundary does not have to be guessed from a clock:

- a turn **opens** when they send
- it **stays open** while they are typing
- it **closes** after twelve seconds of neither, or as soon as somebody else
  speaks

This makes the system *faster*, not slower: the current debounce waits a fixed
forty-five seconds even when the person finished long ago.

## Turns are not stored

A message keeps its own row. The turn is worked out when the rows are read.

The reason is that at the moment a message arrives **it is not yet known
whether the turn is over** — the next message is three seconds away and has not
happened. A stored `turn_id` would be a value that is wrong for as long as the
turn is still running, and would have to be revised. Joining the messages into
one row instead loses the per-message id that deduplicates two delivery paths,
and the reply target on each.

The read-time grouping that reaches the extractor already exists — it was added
to patch the burst hole. It collapses into this rather than living beside it.

## Acceptance criteria

- [ ] Messages from one person, uninterrupted and within the window, are
      classified once and extracted from once
- [ ] A typing signal from that person keeps the turn open past the window
- [ ] Somebody else speaking closes the turn immediately
- [ ] The recovery sweep produces the same turns as the live path — history has
      no typing signals, so the boundary there is timestamps alone
- [ ] Each message still has its own row, its own id and its own reply target
- [ ] The separate read-time grouping added for the burst is gone, not left
      beside this
- [ ] A test drives the burst above and asserts one classification, and that
      the curl reaches the task
