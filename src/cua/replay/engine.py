"""Deterministic replay: run a capability without the model in the loop.

This is the production path. Given an artifact and a set of inputs it walks the
recorded steps, verifies each checkpoint, classifies anything unexpected against
the artifact's own outcome rules, and hands back a structured result.

No LLM is imported here, and none is reachable from here. That is the point of
the whole design: discovery pays for the reasoning once, and every invocation
afterwards is a cheap, repeatable, auditable execution.
"""

from dataclasses import dataclass, field
from pathlib import Path

from cua.artifact import params
from cua.artifact.schema import Capability, OutcomeRule, Step, TargetSpec
from cua.escalation.broker import Escalation, Intervention, StuckKind
from cua.policy.allowlist import Denial, Policy
from cua.replay.checkpoint import describe, is_satisfied, wait_until
from cua.replay.outcomes import ReplayResult, StepRecord, extract_value, find_outcome
from cua.shared.result import Err
from cua.surface.base import Control, Observation, Surface, Target, match_control

# A restart re-runs the flow from the first step, for conditions like a session
# timeout where the only sane recovery is to start again.
MAX_RESTARTS = 1


@dataclass(slots=True)
class _Restart:
    """Internal signal that the run should begin again."""

    reason: str


@dataclass(slots=True)
class _Escalate:
    """Internal signal that a person has to decide before this step can run."""

    kind: StuckKind
    reason: str


@dataclass(slots=True)
class _State:
    """Bookkeeping that survives across the steps of one attempt."""

    outputs: dict[str, str] = field(default_factory=dict)
    recoveries: list[str] = field(default_factory=list)
    records: list[StepRecord] = field(default_factory=list)
    steps_run: int = 0
    # Steps a person has already been asked about, so one stuck step cannot
    # page the same operator in a loop.
    escalated: set[str] = field(default_factory=set)
    interventions: list[str] = field(default_factory=list)


def _trail(state: _State) -> list[str]:
    """One readable line per step performed, for the evidence log."""
    return [
        f"{record.step_id} {record.action}"
        + (f" via={record.matched_by}" if record.matched_by else "")
        for record in state.records
    ]


def to_target(spec: TargetSpec) -> Target:
    """Converts a recorded target into the form the surface matches against."""
    return Target(
        role=spec.role,
        name=spec.name,
        label=spec.label,
        frame=spec.frame,
        index=spec.index,
    )


class ReplayEngine:
    """Executes one capability against a live surface."""

    def __init__(
        self,
        surface: Surface,
        capability: Capability,
        evidence_dir: Path | None = None,
        policy: Policy | None = None,
        escalation: Escalation | None = None,
    ) -> None:
        self.surface = surface
        self.capability = capability
        self.evidence_dir = evidence_dir
        # Defaults to the artifact's own application and nothing else.
        self.policy = policy or Policy.for_capability(capability)
        # Without one, a stuck run simply reports; it never blocks on a person.
        self.escalation = escalation

    def run(self, values: dict[str, str]) -> ReplayResult:
        """Validates inputs, then replays the flow, restarting if asked to."""
        checked = params.validate(self.capability, values)
        if isinstance(checked, Err):
            return ReplayResult(status="rejected", message=checked.error)

        # Recoveries are carried across restarts: a run that signed on twice
        # should say so, even though the second attempt starts from step one.
        recoveries: list[str] = []
        for _ in range(MAX_RESTARTS + 1):
            outcome = self._attempt(values, recoveries)
            if isinstance(outcome, _Restart):
                continue
            return outcome
        return ReplayResult(
            status="failure",
            message=f"Gave up after {MAX_RESTARTS + 1} attempts",
            recoveries=recoveries,
        )

    def _attempt(self, values: dict[str, str], recoveries: list[str]) -> ReplayResult | _Restart:
        """One pass through every recorded step."""
        state = _State(recoveries=recoveries)
        index = 0
        while index < len(self.capability.steps):
            step = self.capability.steps[index]
            decided = self._run_step(step, values, state)
            if decided is None:
                index += 1
                continue
            if isinstance(decided, _Restart):
                return decided
            resolved = self._escalate(step, decided, state)
            if resolved is None:
                # A person unblocked it. Whether the step still needs running is
                # decided by its own checkpoint, not by their word for it: if the
                # operator completed the step by hand the checkpoint already
                # holds, and if they only cleared an obstacle it does not.
                if self._checkpoint_holds(step):
                    index += 1
                continue
            return resolved
        return ReplayResult(
            status="success",
            message="Completed",
            outputs=state.outputs,
            steps_run=state.steps_run,
            recoveries=state.recoveries,
            interventions=state.interventions,
            trail=_trail(state),
        )

    def _run_step(
        self,
        step: Step,
        values: dict[str, str],
        state: _State,
    ) -> ReplayResult | _Restart | _Escalate | None:
        """Performs one step. Returns a result only if the run should stop here."""
        refused = self.policy.check_step(step)
        if refused is not None:
            return self._blocked(refused, state)

        # Irreversible steps are approved before they happen, not explained after.
        if self.policy.needs_human(step) and step.id not in state.escalated:
            return _Escalate("risky_step", f"step {step.id} is irreversible: {step.description}")

        acted = self._act(step, values, state)
        if acted is not None:
            return acted
        state.steps_run += 1

        # Checked on the URL actually reached, so a redirect off the allowlist
        # is caught even though the requested address was fine.
        strayed = self.policy.check_url(self.surface.observe().url)
        if strayed is not None:
            return self._blocked(strayed, state)

        # Outcomes are checked before the checkpoint so a known condition is
        # reported immediately rather than after the checkpoint's timeout.
        decided = self._classify(self.surface.observe(), state)
        if decided is not None:
            return decided

        if step.action == "extract":
            return self._extract(state)
        if step.checkpoint is not None:
            return self._verify(step, state)
        return None

    def _act(
        self,
        step: Step,
        values: dict[str, str],
        state: _State,
    ) -> ReplayResult | None:
        """Carries out a step's action, or reports why it could not."""
        if step.action in ("wait_for", "extract"):
            return None

        filled = ""
        if step.value is not None:
            resolved = params.fill(step.value, values)
            if isinstance(resolved, Err):
                return ReplayResult(status="rejected", message=resolved.error)
            filled = resolved.value

        if step.action == "navigate":
            refused = self.policy.check_url(filled)
            if refused is not None:
                return self._blocked(refused, state)
            self.surface.open(filled)
            state.records.append(StepRecord(step.id, step.action))
            return None

        return self._act_on_control(step, filled, state)

    def _act_on_control(self, step: Step, filled: str, state: _State) -> ReplayResult | None:
        """Finds the step's target and clicks or types into it."""
        if step.target is None:
            return self._failure(step, "a target", "the step records none", state)

        found = self._find(step.target)
        if found is None:
            return self._failure(step, str(to_target(step.target)), "no matching control", state)

        control, matched_by = found
        state.records.append(StepRecord(step.id, step.action, matched_by, step.target.note))
        if step.action == "click":
            self.surface.click(control)
        else:
            self.surface.fill(control, filled)
        return None

    def _find(self, spec: TargetSpec) -> tuple[Control, str] | None:
        """Resolves a recorded target against the current screen."""
        return match_control(self.surface.observe(), to_target(spec))

    def _classify(self, observation: Observation, state: _State) -> ReplayResult | _Restart | None:
        """Turns a recognised condition on screen into a decision."""
        rule = find_outcome(self.capability, observation)
        if rule is None:
            return None
        if rule.kind == "business":
            return ReplayResult(
                status="business_outcome",
                outcome=rule.name,
                message=rule.message,
                steps_run=state.steps_run,
                recoveries=state.recoveries,
                trail=_trail(state),
            )
        if rule.kind == "hard":
            return ReplayResult(
                status="failure",
                message=rule.message,
                observed=rule.when_text,
                steps_run=state.steps_run,
                recoveries=state.recoveries,
                trail=_trail(state),
                screenshot=self._capture(rule.name),
            )
        return self._recover(rule, state)

    def _recover(self, rule: OutcomeRule, state: _State) -> ReplayResult | _Restart | None:
        """Handles a recoverable condition. Returning None means carry on."""
        state.recoveries.append(rule.name)
        recovery = rule.recovery
        if recovery is None:
            return None
        if recovery.kind == "restart":
            return _Restart(rule.name)
        if recovery.kind == "click" and recovery.target is not None:
            found = self._find(recovery.target)
            if found is None:
                return ReplayResult(
                    status="failure",
                    message=f"Could not dismiss {rule.name}",
                    expected=str(to_target(recovery.target)),
                    observed="no matching control",
                    steps_run=state.steps_run,
                    screenshot=self._capture(rule.name),
                )
            self.surface.click(found[0])
        return None

    def _extract(self, state: _State) -> ReplayResult | None:
        """Reads every declared output off the current screen."""
        observation = self.surface.observe()
        for output in self.capability.outputs:
            value = extract_value(output.extract, observation)
            if value is None:
                return ReplayResult(
                    status="failure",
                    message=f"Could not read output {output.name!r}",
                    expected=str(output.extract),
                    observed="not found on screen",
                    steps_run=state.steps_run,
                    recoveries=state.recoveries,
                    trail=_trail(state),
                    screenshot=self._capture("extract"),
                )
            state.outputs[output.name] = value
        return None

    def _verify(self, step: Step, state: _State) -> ReplayResult | _Restart | None:
        """Waits for the step's checkpoint, and explains it if it never holds."""
        assert step.checkpoint is not None
        held, observation = wait_until(step.checkpoint, self.surface.observe)
        if held:
            return None
        # A condition that appeared while we were waiting explains the failure.
        decided = self._classify(observation, state)
        if decided is not None:
            return decided
        return self._failure(step, describe(step.checkpoint), observation.title, state)

    def _escalate(
        self,
        step: Step,
        decided: ReplayResult | _Escalate,
        state: _State,
    ) -> ReplayResult | None:
        """Offers a stuck step to a person. Returning None means try the step again."""
        kind, reason = self._stuck(decided)
        if kind is None or self.escalation is None or step.id in state.escalated:
            return decided if isinstance(decided, ReplayResult) else self._no_operator(step, state)

        state.escalated.add(step.id)
        intervention = Intervention.raise_for(
            capability=self.capability.name,
            goal=self.capability.description,
            step_id=step.id,
            kind=kind,
            reason=reason,
            observation=self.surface.observe(),
            screenshot=self._capture(f"{step.id}-escalated"),
        )
        state.interventions.append(intervention.id)
        resolution = self.escalation.request(intervention, self.surface)
        if resolution.outcome == "resumed":
            return None
        return ReplayResult(
            status="needs_human",
            message=f"Operator did not complete the run: {resolution.note}",
            failed_step=step.id,
            steps_run=state.steps_run,
            recoveries=state.recoveries,
            interventions=state.interventions,
            trail=_trail(state),
            screenshot=intervention.screenshot,
        )

    def _checkpoint_holds(self, step: Step) -> bool:
        """Whether this step's success condition is already true right now."""
        if step.checkpoint is None:
            return False
        return is_satisfied(step.checkpoint, self.surface.observe())

    def _stuck(self, decided: ReplayResult | _Escalate) -> tuple[StuckKind | None, str]:
        """Whether this outcome is one a person should be asked about."""
        if isinstance(decided, _Escalate):
            return decided.kind, decided.reason
        if decided.status == "failure":
            return "hard_failure", decided.message
        return None, ""

    def _no_operator(self, step: Step, state: _State) -> ReplayResult:
        """What a risky step becomes when nobody is available to approve it."""
        return ReplayResult(
            status="needs_human",
            message=f"Step {step.id} needs a person to approve it and none is available",
            failed_step=step.id,
            steps_run=state.steps_run,
            recoveries=state.recoveries,
            interventions=state.interventions,
            trail=_trail(state),
        )

    def _blocked(self, denial: Denial, state: _State) -> ReplayResult:
        """Reports a refusal. Nothing is broken, so this is not a failure."""
        return ReplayResult(
            status="blocked",
            message=str(denial),
            steps_run=state.steps_run,
            recoveries=state.recoveries,
            trail=_trail(state),
        )

    def _failure(self, step: Step, expected: str, observed: str, state: _State) -> ReplayResult:
        """Builds a failure a human can debug without rerunning anything."""
        return ReplayResult(
            status="failure",
            message=f"Step {step.id} failed: {step.description}",
            failed_step=step.id,
            expected=expected,
            observed=observed,
            steps_run=state.steps_run,
            recoveries=state.recoveries,
            trail=_trail(state),
            screenshot=self._capture(step.id),
        )

    def _capture(self, label: str) -> str:
        """Saves a screenshot for the failure report, if evidence is being kept."""
        if self.evidence_dir is None:
            return ""
        path = self.evidence_dir / f"{self.capability.name}-{label}.png"
        self.surface.screenshot(path)
        return str(path)
