"""Turning a successful discovery run into a capability artifact.

This is the compile step, and it is where the run stops being a transcript.

The literal values the model typed are replaced by the parameters they came
from, so a run that looked up member 12345 becomes a capability that looks up
any member. Credentials become sensitive parameters that are declared but never
stored. Each action's stated expectation becomes the step's checkpoint. Outcome
rules are taken from the application's profile rather than from this one run,
because they are a property of the app.
"""

from datetime import UTC, datetime

from cua.artifact.schema import (
    AppRef,
    Capability,
    Checkpoint,
    Extraction,
    OutputSpec,
    ParamSpec,
    RecordMeta,
    Step,
    TargetSpec,
)
from cua.discovery.agent import DiscoveryRun, RecordedAction
from cua.discovery.profiles import load_outcomes
from cua.policy.redact import Redactor
from cua.surface.base import Control


def _strategy(control: Control) -> str:
    """The strongest way this control could be identified when it was recorded."""
    if control.name:
        return "name"
    if control.label:
        return "label"
    return "index"


def _target(control: Control) -> TargetSpec:
    """Records how to find a control again, and how confident that is."""
    strategy = _strategy(control)
    notes = {
        "name": "Has an accessible name.",
        "label": "No accessible name; identified by the visible text beside it.",
        "index": "No name or label; identified by position among controls of its role.",
    }
    # The nearest text to a named control is often the record it sits beside -
    # a member's name in a results row. That is regulated data and it has no
    # business in an artifact, so it is only kept when it is the identifier.
    label = control.label if strategy == "label" else ""
    return TargetSpec(
        role=control.role,
        name=control.name,
        label=label,
        frame=control.frame,
        index=control.index,
        recorded_strategy=strategy,  # type: ignore[arg-type]
        note=notes[strategy],
    )


def _parameterise(value: str, values: dict[str, str]) -> str:
    """Replaces a literal the model typed with the parameter it came from."""
    for name, supplied in values.items():
        if supplied and value == supplied:
            return f"{{{{{name}}}}}"
    return value


def _checkpoint(action: RecordedAction) -> Checkpoint | None:
    """Turns the model's stated expectation into an assertion for replay."""
    if not action.expect_text or not action.checkpoint_held:
        return None
    return Checkpoint(kind="text_present", value=action.expect_text)


def _step(
    index: int,
    action: RecordedAction,
    values: dict[str, str],
    scrub: Redactor,
) -> Step:
    """Converts one recorded action into a replayable step."""
    if action.secret_name:
        value: str | None = f"{{{{{action.secret_name}}}}}"
    elif action.value:
        value = _parameterise(action.value, values)
    else:
        value = None
    return Step(
        id=f"s{index}",
        action=action.action,  # type: ignore[arg-type]
        description=scrub.text(action.why),
        target=_target(action.control) if action.control else None,
        value=value,
        checkpoint=_checkpoint(action),
    )


def _param(name: str, value: str, sensitive: bool) -> ParamSpec:
    """Declares a parameter, inferring its shape from the value used at record time."""
    if sensitive:
        return ParamSpec(
            name=name,
            type="string",
            description=f"{name}, supplied per call and never stored.",
            sensitive=True,
        )
    if value.isdigit():
        return ParamSpec(
            name=name,
            type="integer",
            description=f"{name} used during discovery: {value}.",
            pattern=rf"^\d{{{len(value)}}}$",
        )
    return ParamSpec(name=name, type="string", description=f"{name}.")


def record(
    run: DiscoveryRun,
    name: str,
    product: str,
    base_url: str,
    values: dict[str, str],
    secrets: list[str],
    version: int = 1,
    tenant: str = "",
) -> Capability:
    """Compiles a successful discovery run into a versioned capability."""
    # Anything the model wrote in prose is untrusted text: during discovery it
    # was looking at a real member's record, and it will happily mention them.
    scrub = Redactor(secrets=frozenset(v for v in values.values() if v))

    # Discovery is handed an open page; replay starts from nothing, so the
    # navigation that got us there has to be the first recorded step.
    opening = Step(
        id="s1",
        action="navigate",
        description="Open the application.",
        value=base_url,
    )
    steps = [opening]
    steps += [_step(i, action, values, scrub) for i, action in enumerate(run.actions, start=2)]
    outputs = [
        OutputSpec(
            name=output.name,
            type="string",
            # Derived rather than quoted: the model's own wording for this named
            # the member and the amount it had just read off the screen.
            description=f"Read from the {output.row_contains!r} row, cell {output.cell}.",
            extract=Extraction(
                kind="row_cell",
                row_contains=output.row_contains,
                cell=output.cell,
            ),
        )
        for output in run.outputs
    ]
    if outputs:
        steps.append(
            Step(
                id=f"s{len(steps) + 1}",
                action="extract",
                description="Read the declared outputs from the screen.",
            )
        )

    params = [_param(key, value, sensitive=False) for key, value in values.items()]
    params += [_param(key, "", sensitive=True) for key in secrets]

    return Capability(
        name=name,
        version=version,
        # The operator's goal, not the model's summary. The summary described
        # the specific member it happened to look at; it stays in the evidence.
        description=run.goal,
        app=AppRef(product=product, tenant=tenant, base_url=base_url),
        inputs=params,
        outputs=outputs,
        steps=steps,
        outcomes=load_outcomes(product),
        recorded=RecordMeta(
            recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
            model=run.model,
            llm_steps=run.llm_turns,
        ),
    )
