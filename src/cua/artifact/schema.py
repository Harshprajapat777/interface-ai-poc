"""The capability artifact: what a discovery run turns into.

The artifact is the contract between the model that discovered a flow and the
agent that will invoke it in production. That drives four choices:

1. It is a *contract*, not a transcript. Typed inputs in, typed outputs out,
   and a success condition - so a calling agent knows what it needs and what
   it gets back without reading any steps.

2. Targeting is recorded as the durable description of a control (role, name,
   nearest label, frame, position) plus which strategy actually resolved it at
   record time. A step that only matched on position is visibly fragile, so a
   reviewer can see the weak link before it fails in production.

3. The error taxonomy lives here as data, not as code in the replay engine.
   Adding "this app says 'Record locked' when a teller has it open" is an edit
   to an artifact, not a change to the engine - which is what makes this
   survivable across hundreds of apps.

4. Everything is versioned and reviewable. Artifacts carry an approval state
   so an unattended replay can be gated on a human having read it.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Bumped only when the artifact format itself changes shape.
SCHEMA_VERSION: Literal[1] = 1

ValueType = Literal["string", "integer", "money", "date", "boolean"]


class Strict(BaseModel):
    """Base for every artifact model: unknown fields are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ParamSpec(Strict):
    """One typed input the calling agent has to supply."""

    name: str
    type: ValueType
    description: str
    required: bool = True
    # Checked before the browser is even opened, so bad input fails fast and cheap.
    pattern: str | None = None
    # Sensitive values are supplied at call time and never written to disk.
    sensitive: bool = False


class Extraction(Strict):
    """How to pull one output value off the screen.

    `row_cell` is the workhorse on legacy tables: find the row containing a
    known label, take a cell from it. It survives column reordering far better
    than a positional selector and does not care about the markup at all.
    """

    kind: Literal["row_cell", "pattern"]
    # row_cell: the row is found by this text, then this cell is taken from it.
    row_contains: str | None = None
    cell: int | None = None
    # pattern: a regex with exactly one capture group, matched against visible text.
    pattern: str | None = None


class OutputSpec(Strict):
    """One typed value the capability returns to its caller."""

    name: str
    type: ValueType
    description: str
    extract: Extraction


class TargetSpec(Strict):
    """The durable description of a control, plus how well it was pinned down.

    This is deliberately not a CSS selector. Selectors encode the markup, and
    on a legacy app the markup is the least stable thing there is.
    """

    role: str
    name: str = ""
    label: str = ""
    frame: str = ""
    index: int | None = None
    # Which strategy resolved this control when it was recorded.
    recorded_strategy: Literal["name", "label", "partial_name", "index"] = "name"
    # Free text from the discovery run about why this identification was chosen.
    note: str = ""


class Checkpoint(Strict):
    """A condition asserted after a step, to confirm the step actually worked."""

    kind: Literal["text_present", "text_absent", "url_contains", "control_present"]
    value: str
    timeout_ms: int = 10_000


ActionKind = Literal["navigate", "click", "fill", "wait_for", "extract"]

# Actions that change state the institution cannot simply undo. Replay treats
# these conservatively - see the policy module.
RiskLevel = Literal["safe", "risky"]


class Step(Strict):
    """One recorded action, with how to find its target and how to verify it."""

    id: str
    action: ActionKind
    # Written for a human reviewer, not for the engine.
    description: str
    target: TargetSpec | None = None
    # Literal text, or a {{param}} placeholder filled from the call's inputs.
    value: str | None = None
    checkpoint: Checkpoint | None = None
    risk: RiskLevel = "safe"


class Recovery(Strict):
    """What replay should do about a recoverable condition."""

    kind: Literal["click", "retry", "restart"]
    target: TargetSpec | None = None
    max_attempts: int = 2


class OutcomeRule(Strict):
    """A runtime condition this app is known to produce, and what it means.

    Keeping these in the artifact is the point. "No such member" is a business
    answer, "session expired" is recoverable, and "Application error" is a hard
    failure - and which is which is a property of the app, not of the engine.
    """

    name: str
    # The text on screen that identifies this condition.
    when_text: str
    kind: Literal["business", "recoverable", "hard"]
    # What the caller is told when this fires.
    message: str
    recovery: Recovery | None = None


class AppRef(Strict):
    """Which application, and whose instance of it.

    product plus product_version is what makes an artifact shareable: two
    tenants on the same vendor build can run the same steps. tenant is what
    makes a specialisation traceable back to one institution.
    """

    product: str
    product_version: str = ""
    tenant: str = ""
    base_url: str


class RecordMeta(Strict):
    """Provenance: where this artifact came from, so a reviewer can judge it."""

    recorded_at: str
    # Which model discovered the flow, and how much work it took.
    model: str = ""
    llm_steps: int = 0
    evidence_run_id: str = ""


class Capability(Strict):
    """A reusable, agent-invocable capability produced by one discovery run."""

    schema_version: Literal[1] = SCHEMA_VERSION
    # Stable identifier the calling agent uses, e.g. get_savings_balance.
    name: str
    version: int = 1
    description: str
    app: AppRef

    inputs: list[ParamSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    steps: list[Step]
    outcomes: list[OutcomeRule] = Field(default_factory=list)

    # A base capability this one specialises. The seam for cross-tenant reuse:
    # a tenant override carries only the steps that differ, not a whole copy.
    extends: str | None = None

    # Unattended replay can be gated on this - see scope.md.
    approval: Literal["draft", "approved"] = "draft"
    recorded: RecordMeta

    def input_names(self) -> set[str]:
        """The parameter names a caller may supply."""
        return {param.name for param in self.inputs}

    def sensitive_names(self) -> set[str]:
        """Parameters whose values must never be written to an artifact or a log."""
        return {param.name for param in self.inputs if param.sensitive}

    def tool_schema(self) -> dict[str, object]:
        """Renders the capability as a JSON-Schema tool definition for a calling agent."""
        properties: dict[str, object] = {}
        required: list[str] = []
        for param in self.inputs:
            spec: dict[str, object] = {
                "type": _json_type(param.type),
                "description": param.description,
            }
            if param.pattern:
                spec["pattern"] = param.pattern
            properties[param.name] = spec
            if param.required:
                required.append(param.name)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


def _json_type(value_type: ValueType) -> str:
    """Maps our value types onto JSON Schema types."""
    return "integer" if value_type == "integer" else "string"

