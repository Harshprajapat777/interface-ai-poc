# Design write-up

## 1. Architecture

The system has one idea in it: **the model is a compiler, not a runtime.** An LLM
works out how to drive an application once, that run is compiled into a typed
artifact, and production executes the artifact with no model anywhere in the loop.
Discovery pays for the reasoning; every invocation afterwards is cheap, repeatable
and auditable.

That splits the code into two paths over shared vocabulary:

```
DISCOVERY (once, LLM)                      REPLAY (every call, no LLM)
goal in English                            artifact + typed params
  observe -> decide -> act  --emits-->  ARTIFACT  -->  execute, verify, classify
                                       (versioned JSON)        |
                                                        structured result
```

Seven modules, dependencies pointing one way. `surface` perceives and acts.
`artifact` is the contract. `discovery` and `replay` both sit on those two and
neither knows the other exists — `replay` cannot reach a model even by accident,
which is the property worth having. `policy`, `evidence` and `escalation` are
cross-cutting; `shared` depends on nothing.

**Single process, files on disk, synchronous.** No queue, no service split, no
database. §7 says scaling infrastructure earns nothing, and the boundaries that
would matter at scale are the module ones, which are already there. Artifacts are
a directory of readable JSON precisely so they can be reviewed in a pull request
and diffed between versions; a registry service would swap `artifact/store.py`
and nothing above it would notice.

**Trade-off accepted:** every `observe()` re-scans the page, so replay is slower
than a hand-written Playwright script. In exchange, nothing holds stale element
handles, and the same code path works whether a step follows a navigation, a
frame swap, or a human taking over the browser.

## 2. Artifact schema

The artifact is a **contract**, not a transcript. A calling agent needs to know
what a capability wants, what it returns, and how it will know it worked — without
reading any steps. So a `Capability` carries typed `inputs`, typed `outputs` with
extraction rules, ordered `steps` each with a `checkpoint`, `outcomes`, an
`approval` state, and `recorded` provenance. `tool_schema()` projects it into a
JSON-Schema tool definition an agent can discover and call by name.

Three decisions carry the design.

**Targets are descriptions, not selectors.** A step records `role`, accessible
`name`, the nearest visible `label`, `frame`, and `index` — never CSS. Selectors
encode markup, and on a legacy app the markup is the least stable thing available.
Crucially it also records `recorded_strategy`: which of those actually resolved the
control at record time. A step that only matched on position is then *visibly*
fragile to a reviewer rather than silently fragile in production.

**The error taxonomy lives in the artifact, as data.** Whether "No member found"
is a business answer and "Your session has expired" is recoverable is a property
of *the application*, not of the executor. Putting those rules in the engine would
mean every newly discovered condition across thousands of app instances is a code
change. As data, it is an artifact edit — and rules are maintained per product in
`app_profiles/`, because they are the same for every capability recorded against
that app and every tenant running it.

**Extraction prefers `row_cell`** — "find the row containing `Share Savings`, take
cell 2". This came out of the surface work: legacy table rows arrive as one line of
tab-separated cells. Keying off row content survives column reordering and ignores
markup entirely.

Models are strict (`extra="forbid"`, frozen), so a typo in an artifact is an error
rather than a silently ignored field.

## 3. Determinism & error handling

Determinism comes from three things: no model, explicit targeting with a fallback
chain, and a checkpoint on every screen-changing step.

**The locator chain** is `exact name → nearest label → partial name → position`,
strongest first, and the engine reports which link fired. On the target app this
is not theoretical: submit buttons have accessible names from their `value`, the
search box carries a `title`, and the sign-on inputs have *neither* — they are
found by the text in the table cell beside them, which is exactly what a human
uses.

**Checkpoints poll rather than sleep.** That is what absorbs a slow load without
paying a fixed pause on every step of every run, and a checkpoint that never holds
produces a failure naming the step, what was expected and what was seen.

**The result contract has six statuses, and collapsing any of them loses something
the caller needs:**

| | |
| --- | --- |
| `success` | Finished, outputs attached |
| `business_outcome` | The app answered, and the answer was a legitimate no |
| `rejected` | The caller's input was wrong; the browser never opened |
| `blocked` | Policy declined; nothing is broken |
| `needs_human` | A person was asked and did not finish |
| `failure` | Something broke — step, expected, observed, screenshot |

"No such member" is something a call-centre agent has to say out loud to a member.
It is not an exception. Equally, `rejected` and `failure` both mean "no result",
but one sends an on-call engineer to the caller's bug and the other to ours.

**Runtime conditions are checked before the checkpoint**, so a recognised state is
reported immediately instead of after a ten-second timeout. Recoverable ones are
handled in place — dismiss a known interstitial, or restart the flow after a
session timeout — and recoveries are carried across a restart, so a run that signed
on twice says so.

**On drift**, which the brief rightly calls secondary: the fallback chain absorbs a
renamed button, `recorded_strategy` makes weak matches visible before they break,
and `trail` on every result reports how each step was resolved. What we do *not*
have is a way to detect that a capability has quietly started matching by position
across many runs; see Cuts.

## 4. Heterogeneity & multi-tenant

**The seam is `surface`.** It defines `Control`, `Observation` and `Target`, and
imports nothing from Playwright. Everything above — discovery, replay, the schema —
is written against that vocabulary. Supporting a different surface means one new
implementation.

This is why role and accessible name are computed rather than taken from the
browser's accessibility tree: we also need the *fallback* signals (nearest text,
position among peers) that the tree does not carry. The shape is the same, so a
desktop implementation reading UIAutomation or AT-SPI produces the same
`Observation` — those APIs expose role and name natively, and "the label beside
it" is as meaningful on a Win32 form as in a `<td>`. A screenshot-plus-coordinates
implementation would also fit, though its targets would be far weaker, which is
precisely what `recorded_strategy` would then record.

**For multi-tenant reuse**, an artifact separates what is shared from what is not.
`AppRef` carries `product` and `product_version` (shared by every institution on
that vendor build) apart from `tenant` and `base_url` (theirs alone). `rebase()`
moves a capability to another institution's address without re-recording, and
outcome rules live in the product profile rather than in any one recording.

The rest is designed but not built. `extends` is in the schema as the override
seam: a tenant that renames a button carries *only* that difference, so an
improvement to the base flow reaches every institution instead of being
re-discovered per tenant. Drift detection I would build on the signal already
being recorded — a capability whose steps begin resolving via weaker strategies,
or whose checkpoints start needing retries, has drifted, and that is a scheduled
canary replay per tenant rather than new machinery.

## 5. Escalation & handoff

Three things make the handoff real rather than decorative.

**The person gets the same session** — the browser already signed on and three
screens deep, not a fresh one. Starting over would lose that state, and on a real
core system the second session would often be refused because the first still
holds the record lock.

**Exactly one party is in control, and it is written down.** `ControlTransfer`
holds an owner, acting out of turn raises, and `current()` answers who has it.
Control returns to the automation even if the operator's console throws — a
session nobody owns is the one state a run cannot recover from.

**Stuck is detected at two points, for opposite reasons.** An irreversible step is
approved *before* it happens. A hard failure is offered to a person *after*. Each
step escalates at most once, so one stuck step cannot page an operator in a loop.
The request carries what someone needs to pick up a run cold: capability, goal,
step, why it stopped, the screen, a screenshot.

**Resuming is decided by the step's own checkpoint, not the operator's word.** If
they completed the step by hand the checkpoint already holds and the run moves on;
if they only cleared an obstacle it does not and the step runs again. Taking their
word would give up the verification that makes replay worth trusting. This was not
the first design — the original always re-ran the step, which broke the moment an
operator finished one.

**What they did is recorded.** We cannot see their mouse, so the evidence is the
screen diff across the handoff plus a Playwright trace, which does capture clicks.

The operator console is a CLI (`poe replay --operator`) and the demo uses a
scripted operator. Both implement the same `Operator` protocol; a real co-browsing
product replaces that one file.

## 6. Safety

**Everything defaults closed.** The allowlist is built from the artifact's own
application, so the safe default needs no configuration and holds when someone
forgets to write any. URLs are checked twice — the address requested *and* the
address reached — because a redirect off the allowlist is exactly what a pre-flight
check alone misses. Action types can be narrowed further, so a read-only capability
can be denied `click` outright.

**Risky steps are refused unless a run explicitly permits them.** An irreversible
action taken wrongly on a member account cannot be undone by retrying, so "nobody
chose" must resolve to the conservative option. The alternative is escalation, not
permission.

**Secrets and PII are treated differently, deliberately.** Credentials are never
written anywhere and are never shown to the model: it calls `fill_secret` with a
*name* and the value is substituted locally, so a password reaches the browser
without reaching the API. Member data is returned to the caller — they asked for it
— but masked on the way into any log or artifact, so a balance lookup leaves no
record of whose balance it was. Obvious shapes are caught even when nobody declared
them, because a forgotten flag should not be the difference between safe and not.

Three PII leaks got through the first version and were only caught by inspecting a
real artifact: the model's own summary named the member and quoted the balance, an
output description did the same, and a results-row button recorded the member's
name as its nearest label. Fixed by construction rather than by filtering — the
description is now the operator's goal, output descriptions are derived from the
extraction rule, and adjacent text is kept only when it is the identifier.

**Limits.** Redaction is pattern and value based, so an unusual identifier format
in free text could survive. The allowlist governs where we navigate, not what a
step *means* — nothing stops a well-formed capability doing something harmful
within its own app, which is what the risky/approval class and human review of
artifacts are for. `approval: draft | approved` exists in the schema but nothing
enforces it yet.

## 7. Cuts

**Cut deliberately, with the seam left real:**

- **Desktop and legacy-web surfaces.** Designed against (§4), not implemented. One
  `Surface` implementation.
- **Multi-tenant overrides.** `extends` and `AppRef` are in the schema and
  `rebase()` works; per-tenant override resolution is not built.
- **The operator console.** A CLI behind the `Operator` protocol, as §3.6 permits.
- **Approval gating.** The state exists; nothing refuses to run a `draft`.
- **`wait_for` as an action.** Removed rather than shipped unused — waiting is what
  a checkpoint does, and a step that waits without asserting is a sleep hiding a
  race.
- **No test for the discovery loop itself.** It needs a faked Anthropic client;
  everything it produces is tested, and the loop is exercised by real runs.

**What I would build next, in order:**

1. **Drift canaries.** Replay each capability against each tenant on a schedule and
   alert on strategy degradation — the signal is already recorded, nothing consumes
   it.
2. **Override resolution for `extends`,** with a real second instance to prove it.
3. **Approval gating,** since unattended replay of an unreviewed artifact is the
   most plausible way this hurts someone.
4. **Multi-hit search results.** The recorded flow assumes one row; a real lookup
   needs row matching, which the schema can express but the recorder cannot yet
   produce.
5. **A bounded LLM recovery for a single step on failure** (§8), recorded as
   evidence and never open-ended.

**What I would change about how I built it.** Three of the bugs that mattered most
— PII in artifacts, a broken `--base-url`, and a discovery run that baked a detour
into the flow — were invisible in tests and only appeared when I generated real
evidence and read it. I would produce the evidence artefact much earlier next time
and treat it as the thing under test.
