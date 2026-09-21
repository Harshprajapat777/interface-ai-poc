# Evidence

One end-to-end thread, recorded as it happened: a genuine LLM discovery run, the
capability it produced, and that capability replayed against every class of
outcome the result contract distinguishes.

Logs are JSON Lines, one event per line, every value passed through redaction on
the way out. `grep` for `test-password` across this directory and `artifacts/`
returns nothing.

| File | What it shows |
| --- | --- |
| `01-discovery.jsonl` | Claude driving the app for the first time: each action, why it took it, what it expected to see, whether that held |
| `01-discovery-final-screen.png` | The screen it finished on |
| `02-replay-success.jsonl` | The saved capability replayed with no model. Returns `$4,182.55` |
| `03-replay-member-not-found.jsonl` | `business_outcome` — the app answered, and the answer was no |
| `04-replay-access-denied.jsonl` | `business_outcome` — entitlement refusal, also an answer |
| `05-replay-recovered-interstitial.jsonl` | An unexpected consent dialog, dismissed in flight; the run still succeeds |
| `06-replay-rejected-bad-input.jsonl` | `rejected` — a malformed member number, caught before the browser opened |
| `07-replay-application-error.jsonl` | `failure` — a hard error, with the step, what was expected and what was seen |
| `07-replay-application-error.png` | The screen at the moment it failed |
| `08-handoff.jsonl` | A run that gets stuck, is unblocked by an operator on the same live session, and finishes |
| `08-handoff-session-trace.zip` | Playwright trace of that session, including the operator's own clicks |

The artifact these replays run is [`../artifacts/get_savings_balance/v1.json`](../artifacts/get_savings_balance/v1.json).
Nothing in it was hand-edited.

## Reading a log

```bash
python -c "import json;[print(json.loads(l)) for l in open('evidence/02-replay-success.jsonl')]"
```

Two fields are worth looking at in particular.

`trail` names the locator strategy that resolved each step — `via=name`,
`via=label`, `via=index`. A run held together by `via=index` is fragile, and
this is where that shows up before production finds out.

`status` is one of five, and they are not interchangeable. `success` and
`business_outcome` are both the application answering. `rejected` means the
caller's input was wrong. `blocked` means policy declined. Only `failure` means
something is broken.

## Regenerating

```bash
uv run poe app            # in one terminal

uv run poe discover --goal "Sign on, look up a member by number, and read their current share savings balance" \
  --name get_savings_balance --url http://127.0.0.1:4173 \
  --param operator_id=opr001 --param member_id=12345 --secret password

uv run poe replay --name get_savings_balance --param operator_id=opr001 --param member_id=12345 --secret password
uv run poe replay --name get_savings_balance --param operator_id=opr001 --param member_id=99999 --secret password
uv run poe replay --name get_savings_balance --base-url "http://127.0.0.1:4173/?chaos=error" \
  --param operator_id=opr001 --param member_id=12345 --secret password
uv run poe handoff --param operator_id=opr001 --param member_id=77777 --secret password
```

Files here were renamed from their timestamped originals so the thread reads in
order. Discovery is a live model run, so a regenerated artifact will differ in
wording and may differ in step count.
