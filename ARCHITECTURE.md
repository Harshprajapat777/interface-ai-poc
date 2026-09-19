# Architecture

How this project is structured, and how we work on it.
Read with [scope.md](scope.md) (decisions) and [task.md](task.md) (the brief).

---

## 1. The shape of the system

One idea drives the whole design:

```
  DISCOVERY (once, LLM in the loop)         REPLAY (every time, no LLM)
  ---------------------------------         --------------------------
  goal in English                           artifact + typed params
        |                                         |
        v                                         v
  observe -> decide -> act  --emits-->  ARTIFACT  -->  execute steps
        |                              (versioned      verify checkpoints
        v                               JSON)          |
  goal reached                                         v
                                                 structured result
```

The LLM is a **compiler**, not a runtime. It runs once to work out the UI, and its
output is frozen into an artifact. Production only ever runs the artifact.

### Modules and the one dependency rule

Arrows point the way dependencies are allowed to flow. Nothing points back.

```
  cli --> discovery --> surface      (perceive + act)
      +-> replay    --> artifact     (the capability contract)
      +-> operator  --> policy       (allowlist, action class, redaction)
                    +-> evidence     (logs, screenshots, traces)
                    +-> escalation   (pause, hand off, resume)
                              |
                              v
                           shared     (types + errors; depends on nothing)
```

| Module | Owns | Deliberately does not know |
| --- | --- | --- |
| `surface` | Perceiving and acting on a live UI: a11y snapshot, click, type, navigate | What a flow or an artifact is |
| `artifact` | The capability schema, versioning, load/save | How any surface works |
| `discovery` | The LLM observe-decide-act loop; emits an artifact | How replay executes |
| `replay` | Deterministic execution + the error taxonomy | Anything about the LLM |
| `policy` | Allowlist, safe vs. risky actions, redaction | The flow being run |
| `evidence` | Structured run log, failure screenshots, traces | Business meaning |
| `escalation` | Detecting stuck, handing the live session to a human, resuming | Why the run got stuck |
| `shared` | Types and error classes used everywhere | Everything |

**Why this matters:** `surface` is the seam the brief asks about in section 3.7. Swapping
Playwright for a desktop a11y driver means implementing one interface — `discovery`,
`replay` and the artifact schema do not change. If a dependency arrow ever needs to point
backwards, the boundary is wrong; fix the boundary, not the import.

---

## 2. Folder structure

```
interface-ai-poc/
├── README.md              # setup + the exact demo commands
├── REPORT.md              # the write-up (7 fixed headings from the brief)
├── ARCHITECTURE.md        # this file
├── scope.md               # decisions and their rationale
├── task.md                # the assignment brief
├── pyproject.toml         # deps, ruff/mypy/pytest config, the `check` task
│
├── src/
│   ├── cua/               # the system itself
│   │   ├── shared/            # types, error classes, small helpers
│   │   ├── surface/           # THE SEAM: perceive + act on a live UI
│   │   │   ├── base.py            # Surface protocol - implement this for a new surface
│   │   │   ├── browser.py         # Playwright implementation
│   │   │   └── snapshot.py        # a11y tree -> compact text for the model
│   │   ├── artifact/
│   │   │   ├── schema.py          # Pydantic models - the capability contract
│   │   │   ├── store.py           # load / save / version
│   │   │   └── locator.py         # how a control is identified, + fallback chain
│   │   ├── discovery/
│   │   │   ├── agent.py           # the observe -> decide -> act loop
│   │   │   ├── prompt.py          # system prompt + tool definitions
│   │   │   └── recorder.py        # successful run -> artifact
│   │   ├── replay/
│   │   │   ├── engine.py          # executes an artifact, no LLM
│   │   │   ├── checkpoint.py      # assert we reached the expected state
│   │   │   └── outcomes.py        # the error taxonomy lives here
│   │   ├── policy/
│   │   │   ├── allowlist.py       # permitted domains, routes, action types
│   │   │   ├── actions.py         # safe/reversible vs. risky/irreversible
│   │   │   └── redact.py          # strip secrets + PII before anything is persisted
│   │   ├── evidence/
│   │   │   └── logger.py          # structured JSONL + failure capture
│   │   ├── escalation/
│   │   │   ├── broker.py          # detect stuck, raise request, transfer control
│   │   │   └── operator.py        # minimal operator surface (mocked, by design)
│   │   └── cli/
│   │       ├── discover.py        # uv run poe discover
│   │       ├── replay.py          # uv run poe replay
│   │       └── operator.py        # uv run poe operator
│   │
│   └── target_app/        # the hostile "legacy" app we automate (a fixture, not product)
│       ├── server.py
│       ├── screens.py         # hand-written HTML: framesets, table soup, no test IDs
│       └── members.py         # seeded members covering every error class
│
├── artifacts/             # saved capabilities, versioned JSON
├── evidence/              # run logs, screenshots, traces (committed for the submission)
└── tests/e2e/             # full discovery + replay threads
```

Unit tests live next to the code as `*_test.py`. Only cross-module threads go in `tests/e2e/`.

**Why this is scalable:** a new surface is one folder under `src/cua/surface/`. A new capability is
one JSON file under `artifacts/`. Neither requires touching the engine.

---

## 3. Build order

Each step ends with a checkpoint: lint + typecheck + tests green, then a short summary to
you before the next one starts.

| # | Step | Done when |
| --- | --- | --- |
| 1 | Repo skeleton - uv, ruff, mypy, pytest | `uv run poe check` passes on an empty project |
| 2 | Target app | All six member scenarios reachable by hand in a browser |
| 3 | `surface` - a11y snapshot, act, evidence capture | Can snapshot + click the target app through frames |
| 4 | `artifact` - Pydantic schema + store | Schema round-trips; a hand-written artifact validates |
| 5 | `replay` - engine, checkpoints, outcomes | Hand-written artifact replays green against the target app |
| 6 | `policy` - allowlist, action class, redaction | Off-allowlist navigation is blocked; no secret reaches disk |
| 7 | `discovery` - the real LLM loop | A genuine Claude run reaches the goal and emits an artifact |
| 8 | `escalation` - pause, hand off, resume | Human drives the same session, automation resumes after |
| 9 | Evidence runs - happy path + one error case | `/evidence/` has both, with logs and screenshots |
| 10 | README.md + REPORT.md | A stranger can clone and run the demo path |

Step 5 before step 7 is deliberate: replay is built and proven against a **hand-written**
artifact first. That way, when the LLM finally runs, we are debugging the model — not the
engine underneath it.

---

## 4. How we work

### Human in the loop

Before each step: what I am about to build and why.
After each step: a short, practical summary — what it does, what changed, what is next.
Plain language, no walls of text. You approve before I move on.

### Every step ends green

```
uv run poe check     # ruff + mypy --strict + pytest, all three
```

Never "fix the tests later." Tests are updated **in the same step** as the code — new
behaviour gets a test, changed behaviour gets its test changed. A step is not done until
`uv run poe check` is clean, and the real result gets reported, including failures.

### Code style

- **Plain names.** `find_member`, not `MemberResolutionStrategyFactory`.
- **Short functions, one job each.** If it needs a paragraph to explain, split it.
- **One short comment above a function** saying what it does — not how, the code shows how.
- **No cleverness.** Boring, obvious code beats elegant code nobody can follow.
- **Typed at the edges.** Pydantic validates anything crossing a boundary (artifact, LLM output,
  CLI input). Inside a module, plain typed Python.
- **Errors are values.** Business outcomes are returned, not thrown. Only genuine faults throw.

### Commits

Small and per-step, so the history reads as the build order. Nothing is committed that
`uv run poe check` has not passed.

### Secrets

`ANTHROPIC_API_KEY` lives in `.env`, which is gitignored. No key, no credential, and no
member PII ever reaches `artifacts/`, `evidence/`, or a commit — `policy/redact.py` runs
before anything is written to disk.
