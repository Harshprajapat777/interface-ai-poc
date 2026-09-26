# Computer-use automation

An LLM works out how to drive a legacy application once. That run is compiled
into a typed, versioned artifact. From then on the artifact replays
deterministically, with no model in the loop, and an AI agent invokes it by name.

> The model is a compiler, not a runtime.

Built for [the interface.ai take-home](task.md). The design decisions and their
costs are in [scope.md](scope.md); the write-up is [REPORT.md](REPORT.md); the
module boundaries are in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Why this shape

Banks run back-office applications with no API. Two obvious approaches both fail:
hand-writing a script per flow does not scale to thousands of app instances, and
putting an LLM in the loop of every transaction is slow, costly, non-deterministic
and unauditable. So the model runs **once**, at authoring time, and what it
learned is frozen into something cheap and repeatable.

## Setup

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium
cp .env.example .env
```

Put your key in `.env` — it is gitignored:

```
ANTHROPIC_API_KEY=sk-ant-...
CUA_SECRET_PASSWORD=test-password
```

`CUA_SECRET_PASSWORD` is the dummy credential for the fake app. Secrets are read
from the environment by name, never typed on a command line, and the model is
never shown one.

**Only discovery needs the API key.** Replay never calls a model, so everything
below except the first command runs without one.

## The demo path

Start the target application in one terminal and leave it running:

```bash
uv run poe app          # http://127.0.0.1:4173
```

**1. Discover.** The model drives the app and emits a capability:

```bash
uv run poe discover \
  --goal "Sign on, look up a member by number, and read their current share savings balance" \
  --name get_savings_balance \
  --url http://127.0.0.1:4173 \
  --param operator_id=opr001 --param member_id=12345 \
  --secret password
```

Writes `artifacts/get_savings_balance/v1.json` and a run log under `evidence/`.

**2. Replay, with no model.** Note the different member — the literal the model
typed became a typed parameter:

```bash
uv run poe replay --name get_savings_balance \
  --param operator_id=opr001 --param member_id=77777 --secret password
```

```json
{ "status": "success", "outputs": { "share_savings_balance": "$27.03" },
  "recoveries": ["consent_required"] }
```

That run hit an unexpected consent dialog, recovered from it in flight, and still
returned the balance.

## Seeing the error handling

The fake app can be made to fail on demand. Each of these is a different class of
result, and telling them apart is the point of the whole exercise:

```bash
# business outcome - the app answered, and the answer was no
uv run poe replay --name get_savings_balance --param operator_id=opr001 --param member_id=99999 --secret password

# business outcome - entitlement refusal
uv run poe replay --name get_savings_balance --param operator_id=opr001 --param member_id=55555 --secret password

# rejected - the caller's input was wrong; the browser never opened
uv run poe replay --name get_savings_balance --param operator_id=opr001 --param member_id=abc --secret password

# failure - a hard error, with a screenshot of the screen it died on
uv run poe replay --name get_savings_balance --base-url "http://127.0.0.1:4173/?chaos=error" \
  --param operator_id=opr001 --param member_id=12345 --secret password
```

| Status | Means |
| --- | --- |
| `success` | Finished; the declared outputs are attached |
| `business_outcome` | The application answered, and the answer was a legitimate no |
| `rejected` | The caller's inputs were wrong; nothing was touched |
| `blocked` | Policy declined to act; nothing is broken |
| `needs_human` | A person was asked to step in and did not finish it |
| `failure` | Something broke: the step, what was expected, what was seen |

## Handing a stuck run to a person

```bash
# scripted operator, reproducible - good for seeing the mechanism
uv run poe handoff --param operator_id=opr001 --param member_id=77777 --secret password

# you are the operator: the browser opens and waits for you
uv run poe replay --name get_savings_balance --operator \
  --param operator_id=opr001 --param member_id=77777 --secret password
```

Both delete a known outcome rule so replay genuinely does not recognise the screen
it lands on. It stops, a person works the **same live session**, and the run
resumes. Control is an explicit owner, and the session trace records what they did.

## The fake application

`src/target_app/` is a stand-in for a legacy servicing console: a frameset, table
layouts, positional ids like `ctl00_r1_c2`, no test IDs, no label associations.
Sign on with `opr001` / `test-password`.

It is hostile on purpose. Real back-office applications look like this, and it is
what forces the locator strategy to be something other than CSS selectors.

| Input | What happens |
| --- | --- |
| `12345` | Succeeds, `$4,182.55` |
| `77777` | Consent interstitial, then `$27.03` |
| `99999` | No member found |
| `55555` | Access denied |
| `abc` | Fails validation |
| `?chaos=slow` | Six-second delay |
| `?chaos=expire` | Session timeout |
| `?chaos=error` | Application error |

## Running the checks

```bash
uv run poe check        # ruff + mypy --strict + pytest
uv run poe regression   # with the app running: every runtime condition, 5 replays each
```

134 tests. The end-to-end ones drive a real browser against the real app; the rest
run without one. The regression matrix replays the saved capability against each
condition the app can produce (not found, access denied, consent pop-up, bad input,
slow load, session expiry, application error) and fails if any run disagrees with
its expected status.

## Evidence

[`evidence/`](evidence/) holds one complete thread — a genuine discovery run, the
artifact it produced, and replays covering every result class including a failure
and a human handoff. [`evidence/README.md`](evidence/README.md) explains each file.

## Layout

```
src/cua/surface/      perceiving and acting on a live UI   (the seam)
src/cua/artifact/     the capability schema and store
src/cua/discovery/    the LLM loop, and the compile step
src/cua/replay/       deterministic execution, error taxonomy
src/cua/policy/       allowlist, risk, redaction
src/cua/escalation/   handing the live session to a person
src/cua/evidence/     the run log
src/cua/cli/          discover, replay, handoff
src/target_app/       the hostile fake application
```

Dependencies point one way: `discovery` and `replay` both sit on `surface` and
`artifact`, and neither knows what the other does. Nothing imports Playwright
except `surface/browser.py`.
