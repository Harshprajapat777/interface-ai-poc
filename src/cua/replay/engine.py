"""Deterministic replay: run a capability without the model in the loop.

This is the production path. Given an artifact and a set of inputs it walks the
recorded steps, verifies each checkpoint, classifies anything unexpected against
the artifact's own outcome rules, and hands back a structured result.

No LLM is imported here, and none is reachable from here. That is the point of
the whole design: discovery pays for the reasoning once, and every invocation
afterwards is a cheap, repeatable, auditable execution.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cua.artifact import params
from cua.artifact.schema import Capability, OutcomeRule, Step, TargetSpec
from cua.escalation.broker import Escalation, Intervention, StuckKind
from cua.policy.allowlist import Denial, Policy
from cua.replay.checkpoint import POLL_INTERVAL_SECONDS, describe, is_satisfied, wait_until
from cua.replay.outcomes import ReplayResult, StepRecord, extract_value, find_outcome
from cua.replay.typecheck import conforms
from cua.shared.result import Err
from cua.surface.base import Control, Observation, Surface, Target, match_control

# A restart re-runs the flow from the first step, for conditions like a session
# timeout where the only sane recovery is to start again.
MAX_RESTARTS = 1

# How many times one step may be re-run because a declared "retry" condition
# appeared. Past this the condition is not transient, and saying so is the answer.
MAX_STEP_RETRIES = 2


@dataclass(frozen=True, slots=True)
class Timing:
    """How patient a replay is. Production uses the defaults; tests shrink them.

    Every limit here is a bound, not a pause: a healthy run never waits for any
    of them, and a sick one cannot wait longer than they allow.
    """

    # How long a step's control may take to appear before the step fails.
    target_wait_s: float = 5.0
    # Attempts at one action when the surface itself errors (a detached element,
    # a load that timed out, a connection refused).
    action_attempts: int = 3
    # First pause between attempts; it doubles each time.
    backoff_s: float = 0.5
    # The whole run, restarts included. A caller waiting on a capability gets an
    # answer within this, whatever the application is doing.
    run_budget_s: float = 120.0
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


@dataclass(slots=True)
class _Restart:
    """Internal signal that the run should begin again."""

    reason: str


@dataclass(slots=True)
class _Retry:
    """Internal signal that the current step should be run again after a pause."""

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
    # How many times each step has been re-run for a declared retry condition.
    retries: dict[str, int] = field(default_factory=dict)


def _trail(state: _State) -> list[str]:
    """One readable line per step performed, for the evidence log."""
    return [
        f"{record.step_id} {record.action}"
        + (f" via={record.matched_by}" if record.matched_by else "")
        for record in state.records
    ]


def _first_line(error: BaseException) -> str:
    """The useful part of a surface error, without its multi-line call log."""
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


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
        timing: Timing | None = None,
    ) -> None:
        self.surface = surface
        self.capability = capability
        self.evidence_dir = evidence_dir
        # Defaults to the artifact's own application and nothing else.
        self.policy = policy or Policy.for_capability(capability)
        # Without one, a stuck run simply reports; it never blocks on a person.
        self.escalation = escalation
        self.timing = timing or Timing()
        self._deadline = 0.0

    def run(self, values: dict[str, str]) -> ReplayResult:
        """Validates inputs, then replays the flow, restarting if asked to.

        This never raises. The caller is an agent that needs an answer it can
        act on, and a stack trace is not one: anything the engine did not
        anticipate still comes back as a failure with the error in it.
        """
        checked = params.validate(self.capability, values)
        if isinstance(checked, Err):
            return ReplayResult(status="rejected", message=checked.error)

        self._deadline = self.timing.now() + self.timing.run_budget_s
        # Recoveries are carried across restarts: a run that signed on twice
        # should say so, even though the second attempt starts from step one.
        recoveries: list[str] = []
        try:
            return self._run_attempts(values, recoveries)
        except Exception as error:  # noqa: BLE001 - the last line of defence
            return ReplayResult(
                status="failure",
                message=f"Replay stopped on an unexpected error: {_first_line(error)}",
                observed=type(error).__name__,
                recoveries=recoveries,
                screenshot=self._capture("unexpected"),
            )

    def _run_attempts(self, values: dict[str, str], recoveries: list[str]) -> ReplayResult:
        """Runs the flow, starting again from the top when a restart is called for."""
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
            if self.timing.now() > self._deadline:
                return self._out_of_time(step, state)
            decided = self._run_step(step, values, state)
            if decided is None:
                index += 1
                continue
            if isinstance(decided, _Restart):
                return decided
            if isinstance(decided, _Retry):
                retried = self._retry_step(step, decided, state)
                if retried is not None:
                    return retried
                continue
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

    def _out_of_time(self, step: Step, state: _State) -> ReplayResult:
        """The run has used its whole budget; stop and say where it got to."""
        budget = self.timing.run_budget_s
        return self._failure(step, f"the run to finish within {budget:g}s", "out of time", state)

    def _retry_step(self, step: Step, retry: _Retry, state: _State) -> ReplayResult | None:
        """Pauses before a step is run again, or stops if it has been retried enough."""
        count = state.retries.get(step.id, 0)
        if count >= MAX_STEP_RETRIES:
            return self._failure(
                step,
                f"{retry.reason} to clear within {MAX_STEP_RETRIES} retries",
                f"{retry.reason} persisted",
                state,
            )
        state.retries[step.id] = count + 1
        self.timing.sleep(self.timing.backoff_s * 2**count)
        return None

    def _run_step(
        self,
        step: Step,
        values: dict[str, str],
        state: _State,
    ) -> ReplayResult | _Restart | _Retry | _Escalate | None:
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
    ) -> ReplayResult | _Restart | _Retry | None:
        """Carries out a step's action, or reports why it could not."""
        if step.action == "extract":
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
            problem = self._retrying(step, lambda: self.surface.open(filled), state)
            if problem:
                return self._failure(step, f"{filled} to open", problem, state)
            state.records.append(StepRecord(step.id, step.action))
            return None

        return self._act_on_control(step, filled, state)

    def _act_on_control(
        self, step: Step, filled: str, state: _State
    ) -> ReplayResult | _Restart | _Retry | None:
        """Finds the step's target and clicks or types into it."""
        if step.target is None:
            return self._failure(step, "a target", "the step records none", state)
        target = step.target

        found = self._wait_for(target)
        if found is None:
            return self._explain_missing(step, target, state)

        _, matched_by = found
        state.records.append(StepRecord(step.id, step.action, matched_by, target.note))
        problem = self._retrying(step, lambda: self._perform(step, target, filled), state)
        if problem:
            return self._failure(step, f"{step.action} to complete", problem, state)
        return None

    def _perform(self, step: Step, target: TargetSpec, filled: str) -> None:
        """Clicks or fills a control, resolved afresh so a retry never uses a stale one."""
        found = self._find(target)
        if found is None:
            raise LookupError(f"{to_target(target)} is no longer on screen")
        if step.action == "click":
            self.surface.click(found[0])
        else:
            self.surface.fill(found[0], filled)

    def _retrying(self, step: Step, act: Callable[[], None], state: _State) -> str:
        """Runs an action, retrying surface errors with backoff. Returns the last error.

        Two things stop a retry. An irreversible step is never repeated on a
        guess: if the first attempt half-happened, a second could do it twice.
        And a step whose checkpoint already holds is not repeated at all - the
        error came after the action landed, so the step is done.
        """
        problem = ""
        for attempt in range(self.timing.action_attempts):
            if attempt:
                if step.risk == "risky":
                    break
                self.timing.sleep(self.timing.backoff_s * 2 ** (attempt - 1))
                if self._checkpoint_holds(step):
                    return ""
                state.recoveries.append(f"retried_{step.id}")
            try:
                act()
                return ""
            except Exception as error:  # noqa: BLE001 - reported, not swallowed
                problem = _first_line(error)
        return problem

    def _wait_for(self, spec: TargetSpec) -> tuple[Control, str] | None:
        """Waits a bounded time for a step's control to appear.

        The previous step's checkpoint usually means the screen is ready, but
        not every step has one, and a frame can still be painting. Polling
        costs nothing when the control is already there.
        """
        deadline = self.timing.now() + self.timing.target_wait_s
        while True:
            found = self._find(spec)
            if found is not None or self.timing.now() >= deadline:
                return found
            self.timing.sleep(POLL_INTERVAL_SECONDS)

    def _explain_missing(
        self, step: Step, target: TargetSpec, state: _State
    ) -> ReplayResult | _Restart | _Retry | None:
        """A control that never appeared is often explained by what did appear instead.

        A session-expired page or an error screen is a better answer than "no
        matching control". If the condition was cleared in place - an
        interstitial dismissed - the control gets one more chance to appear.
        """
        recovered_before = len(state.recoveries)
        decided = self._classify(self.surface.observe(), state)
        if decided is not None:
            return decided
        if len(state.recoveries) > recovered_before and self._wait_for(target) is not None:
            return _Retry("control appeared after recovery")
        return self._failure(step, str(to_target(target)), "no matching control", state)

    def _find(self, spec: TargetSpec) -> tuple[Control, str] | None:
        """Resolves a recorded target against the current screen."""
        return match_control(self.surface.observe(), to_target(spec))

    def _classify(
        self, observation: Observation, state: _State
    ) -> ReplayResult | _Restart | _Retry | None:
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

    def _recover(self, rule: OutcomeRule, state: _State) -> ReplayResult | _Restart | _Retry | None:
        """Handles a recoverable condition. Returning None means carry on."""
        state.recoveries.append(rule.name)
        recovery = rule.recovery
        if recovery is None:
            return None
        if recovery.kind == "restart":
            return _Restart(rule.name)
        if recovery.kind == "retry":
            return _Retry(rule.name)
        if recovery.kind == "click" and recovery.target is not None:
            found = self._find(recovery.target)
            if found is None:
                return ReplayResult(
                    status="failure",
                    message=f"Could not dismiss {rule.name}",
                    expected=str(to_target(recovery.target)),
                    observed="no matching control",
                    steps_run=state.steps_run,
                    recoveries=state.recoveries,
                    trail=_trail(state),
                    screenshot=self._capture(rule.name),
                )
            self.surface.click(found[0])
        return None

    def _extract(self, state: _State) -> ReplayResult | None:
        """Reads every declared output off the current screen, and checks its shape."""
        observation = self.surface.observe()
        for output in self.capability.outputs:
            value = extract_value(output.extract, observation)
            if value is None:
                return self._unreadable(output.name, str(output.extract), "not found", state)
            # The raw value stays out of the report: it may be the very data the
            # log is not allowed to hold. Its length is enough to debug with.
            if not conforms(value, output.type):
                observed = f"a value that is not {output.type} ({len(value)} characters)"
                return self._unreadable(output.name, f"a {output.type} value", observed, state)
            state.outputs[output.name] = value
        return None

    def _unreadable(self, name: str, expected: str, observed: str, state: _State) -> ReplayResult:
        """An output that could not be read, or was read as the wrong kind of thing."""
        return ReplayResult(
            status="failure",
            message=f"Could not read output {name!r}",
            expected=expected,
            observed=observed,
            steps_run=state.steps_run,
            recoveries=state.recoveries,
            trail=_trail(state),
            screenshot=self._capture("extract"),
        )

    def _verify(self, step: Step, state: _State) -> ReplayResult | _Restart | _Retry | None:
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
        try:
            self.surface.screenshot(path)
        except Exception:  # noqa: BLE001 - evidence must never replace the error it documents
            return ""
        return str(path)
