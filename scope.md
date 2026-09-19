# Scope — Computer-Use Automation System

Assignment brief: [task.md](task.md)

**Through-line:** the model discovers once, the artifact is the frozen result,
deterministic replay is what production runs. The LLM is a compiler, not a runtime.

---

## 1. Requirement coverage

| Step | Key point | What we're building | Status |
| ---- | --------- | ------------------- | ------ |
| **1** | Business problem — why this system is needed | Legacy bank back-office apps with no API; hand-written scripts don't scale to 1000s of app instances, and a live LLM on a transaction screen is too slow/costly/unauditable | ✅ understood |
| **2** | Computer-use loop — Observe → Decide → Act | LLM loop over an a11y-tree snapshot (+ screenshot fallback), bounded by max-steps / timeout / dead-end | ⏳ |
| **3** | Artifact — save what the AI learned | Zod-defined, versioned JSON capability: typed inputs, ordered steps, locator strategy per step, typed outputs, checkpoints | ⏳ |
| **4** | Deterministic replay — no LLM in the loop | Artifact + params → replay engine → structured result. Literal values captured during discovery become typed parameters | ⏳ |
| **5** | Human-in-the-loop — same live session | Pause → cede control on the *same* browser session → human acts → signal resume → record what they did | ⏳ |
| **6** | Safety & guardrails | Allowlist (domains/routes/action types), safe vs. risky-irreversible action classes, redaction before anything is persisted | ⏳ |
| **7** | Evidence & observability | Structured JSONL run log (what + why), screenshot + a11y snapshot + Playwright trace on failure | ⏳ |
| **8** | Heterogeneity & multi-tenant | Design-only: surface adapter seam, artifact base + per-tenant overrides, drift detection | ⏳ |
| **9** | System architecture | Single process, clear module boundaries: surface / agent / artifact / replay / policy / evidence / escalation | ⏳ |
| **10** | Discovery → Replay → Deliverables | README.md, REPORT.md (7 fixed headings), /evidence/ with both runs incl. one error case | ⏳ |

---

## 2. Decisions

The brief is deliberately under-specified — §1: *"make a decision, and tell us why."*
These are ours. Each one gets defended in `REPORT.md`.

### D1 — Target surface: build our own hostile legacy app
Server-rendered, framesets, nested table layout, no test IDs, inline `onclick`, fake member DB.

**Why:** §3.3 makes runtime error handling load-bearing and §6 asks for evidence of a replay
hitting an exceptional state. We need to trigger *record not found*, *session timeout*,
*permission denial*, and a *surprise confirmation dialog* on demand. A public demo site can't be
made to fail on cue, and its clean DOM answers the easy version of the problem.

**Cost:** ~an afternoon that could have gone to the core. Accepted — it buys the error evidence.

### D2 — Perception & locators: accessibility tree primary, screenshot fallback
Model observes an a11y-tree snapshot; screenshots supplied when the tree is ambiguous.
Locators recorded as `role` + accessible name, with an explicit fallback chain.

**Why:** §3.1 — *"bias toward an approach that would still work when the surface has no clean
DOM."* The a11y tree is the one surface present on legacy web **and** native desktop, which is
also our answer to §3.7's "what's the seam between perceiving a surface and the recorded flow?"

**Cost:** more work than CSS selectors. Accepted — selectors would dodge the actual question.

### D3 — Stack: Python + Playwright + Pydantic
Python 3.11, `uv` for dependencies, `ruff` + `mypy --strict` + `pytest` for checks.
Anthropic SDK for the discovery loop.

**Why:** one Pydantic model yields the typed artifact, runtime validation on replay, *and* JSON
Schema for the agent-facing tool contract. Playwright exposes the a11y tree natively and gives
traces + screenshots free for §3.5.

**On the language itself:** §4 makes this our call and §7 says breadth of frameworks earns
nothing, so Python is not worth points over TypeScript on the rubric. We picked it because this
is an AI-engineering context — it is the language the reviewers read fastest, and the one we can
defend line by line.

### D4 — Real vs. mocked
§5 permits mocking at clean seams, if intentional and documented.

- **Real:** agent loop, artifact schema, replay engine, error taxonomy, guardrails, evidence,
  and the handoff *mechanism* (pause → cede → resume → record).
- **Mocked at a seam:** operator console is a bare CLI/page, not co-browsing (§3.6 allows this).
  Desktop surface and multi-tenant reuse are design-only (§3.7 explicitly does not require them).

### D5 — The flow: `get_savings_balance(member_id)`
Search → results table → detail screen → read balance.

**Why:** multi-step, one real typed input, one real typed output, and every error class lands
naturally — malformed ID → validation, unknown ID → not-found, flagged account → permission
denial, idle → session timeout.

---

## 3. Error taxonomy — the load-bearing piece

§10: *"Conflating [business outcome and failure] is the most common design mistake here."*
Replay returns exactly one of:

| Class | Example | Contract |
| ----- | ------- | -------- |
| **Success** | balance read | `{ ok: true, outputs }` |
| **Business outcome** | "no such member" | `{ ok: true, outcome: 'not_found' }` — a legitimate answer, never an exception |
| **Recoverable** | maintenance interstitial, slow load | handled internally (dismiss / wait-retry), logged, run continues |
| **Hard failure** | Search button gone | `{ ok: false, step, expected, observed, evidence }` |
| **Needs human** | risky irreversible confirmation | pause → escalate with context → human takes the live session |

---

## 4. Out of scope

Per §5 and §7 — *"we do not reward … building scaling infrastructure."*

- Queues, clusters, multi-tenant plumbing
- Real co-browsing operator console
- Desktop/OS automation implementation
- Any real credentials, real PII, or any real bank system

---

## 5. Build order

1. Hostile target app + seeded fake member data
2. Surface adapter (a11y snapshot, act, evidence capture)
3. Artifact schema (Zod) — designed *before* the agent, so discovery emits into a real shape
4. Discovery agent loop → emit artifact
5. Replay engine + error taxonomy
6. Guardrails (allowlist, action classes, redaction)
7. Escalation & handoff
8. Evidence runs: happy path + one error case
9. README.md + REPORT.md
