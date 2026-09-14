# 03: Fill the set and baseline today's classifier against it

**What to build:** the measurement that every later ticket is judged against.
The rows are the operator's to write — a classifier scored against labels a model
chose is measuring nothing (D18) — so this ticket is gated on them. Once the file
is filled and frozen, the **current** classifier is scored against it and the
accuracy, confusion matrix and threshold table are written down. D16 is the whole
point of the ordering: rebuild the ruler first, read today's code with it, then
change the code. Rebuilding and changing at once moves both and the difference
means nothing.

**Blocked by:** 02.

**Status:** ready-for-human

- [ ] The frozen set holds rows at the boundaries between task types, rows a correct classifier should call `skip`, and rows whose turn is several messages. *(Partly: `api_issue` is filled from real traffic; the other three labels and every hard row are still outstanding.)*
- [x] Rows read like the channel does — Vietnamese, a pasted `curl`, a correlationId, a stack trace — rather than like an English description of it. *(True of the seventeen `api_issue` rows; the eight `access_request`/`doc_question` rows are still English textbook.)*
- [x] The coverage guard from ticket 02 passes against the filled file.
- [ ] The current classifier is scored against it and the numbers are recorded where the next person can find them.
- [ ] The cost of that run is written down beside it.

## Comments

**Not done — waiting on the operator.** The tooling, the shape, the coverage
guard and the refresh are in place (ticket 02) and `evals/README.md` says what
the rows have to cover. Filling them in is a data change to
`evals/triage.jsonl`; D18 is explicit that the labels have to be somebody’s
judgement, and a classifier scored against labels a model chose measures
nothing.

**The baseline is taken at `bee022f`, not at `HEAD`, and that is a correction
rather than a detail.** This ticket said "against the current code", which was
true when it was written and stopped being true when ticket 07 shipped ahead of
it — 07 declared itself blocked by this one and was done anyway. Followed today,
that instruction would record an *after* as the baseline, which is exactly the
worthless measurement D16 exists to prevent. Found by a review of the whole
board, not by anyone reading the ticket.

Recovery is still open because `evals/triage.jsonl` has not changed since the
board began: the file at `HEAD` is byte-identical to the file at `b61a7e1`. So
the rows go in on this branch, and then:

    git stash                                     # keep the filled rows
    git checkout bee022f                          # the last commit before 07
    git stash pop                                 # the set, against old code
    uv run python -m evals.run_triage_eval        # the BEFORE reading
    git checkout main && git stash pop            # back, rows intact
    uv run python -m evals.run_triage_eval        # the AFTER reading

Record accuracy, the confusion matrix, the threshold table, the count of
decisions outside the closed set, and what each run cost. Two readings of one
ruler is what D16 asks for; one reading of a ruler that moved is what it
forbids.

**A caveat on the before reading, stated rather than discovered:** `bee022f`
has no out-of-set number, because the concept did not exist yet. Its rows land
as `needs_human` there and as `needs_human` plus a count here, so accuracy is
comparable and that one line is new rather than changed.


## Where this stands, 2026-09-14

**Seventeen rows in, from the operator's own channel** (commit `827296a`).
Supplied verbatim and labelled by them, converted into `SEED` — not into
`triage.jsonl`, which is generated and would lose them on the next refresh.
35 rows now, guard green.

**Not yet a ruler, and the baseline is not worth taking until it is.** The set
is 66% `api_issue`: a classifier answering `api_issue` to everything scores
65.7%, so the headline accuracy can rise while the system gets worse. The
confusion matrix would still tell the truth; the number above it would not.
Spending two eval runs to record a figure that cannot be compared later is the
measurement D16 forbids, arrived at from the other direction.

**What is still the operator's to write**, and why code cannot:

- **`skip` rows that look like work.** The four there are "anyone want lunch".
  What is needed is a message *about* an API that asks for nothing — "hôm qua
  api lỗi nhưng tự hết rồi nhé". It is the only detector for a change that
  makes the model eager to open tasks; without it that failure surfaces weeks
  later as tasks nobody wanted.
- **Rows on the boundary between two types.** "Xin quyền vào repo để fix cái
  500" is an access request or an API issue depending on what this team means
  by it. Nobody else can say.
- **`access_request` and `doc_question` in the channel's own voice.** Those
  eight rows are still full-sentence English and resemble nothing the system
  reads.

**Two loose ends from the conversion:**

- The rows the operator marked "(có CURL bên dưới)" carry the ask without the
  evidence, so they are harder than reality. They become multi-message turns
  the moment the real `curl` bodies are pasted in. Inventing one would be
  putting traffic in a reporter's mouth to make a number look better.
- One row — "nguyên nhân thật - bẫy region interfence của API v2…" — reads
  like the operator explaining a cause rather than a reporter asking. If it is
  the watched account's own message the correct behaviour is to *close* a task,
  not open one. Flagged, label left as given.

**Ticket 07 still owes its eval reading**, and that debt is this ticket's to
clear. The runner is ready and reports the out-of-set count; the two readings
and their order are written above.
