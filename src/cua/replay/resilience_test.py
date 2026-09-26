"""The engine under a misbehaving surface: errors, slowness, and wrong reads.

Each test pins one promise the engine makes to its caller - above all that it
answers with a result, whatever the application or the browser does.
"""

from pathlib import Path

from cua.artifact.schema import (
    AppRef,
    Capability,
    Checkpoint,
    Extraction,
    OutcomeRule,
    OutputSpec,
    RecordMeta,
    Recovery,
    Step,
    TargetSpec,
)
from cua.policy.allowlist import Policy
from cua.replay.engine import ReplayEngine, Timing
from cua.surface.base import Control, Observation

SEARCH = Control(ref="c1", role="button", name="Search", label="", frame="", index=0)


class FlakySurface:
    """A surface whose actions fail a set number of times before working."""

    def __init__(
        self,
        texts: list[str],
        click_failures: int = 0,
        control_after: int = 0,
        after_click: list[list[str]] | None = None,
    ) -> None:
        self.texts = texts
        self.click_failures = click_failures
        # How many observations pass before the Search button is on screen.
        self.control_after = control_after
        self.after_click = after_click
        self.observations = 0
        self.clicks = 0
        self.opens = 0

    def open(self, url: str) -> None:
        self.opens += 1

    def observe(self) -> Observation:
        self.observations += 1
        controls = [SEARCH] if self.observations > self.control_after else []
        return Observation(url="http://app", title="t", controls=controls, texts=self.texts)

    def click(self, control: Control) -> None:
        self.clicks += 1
        if self.clicks <= self.click_failures:
            raise TimeoutError("Locator.click: Timeout 10000ms exceeded.\nCall log: ...")
        if self.after_click:
            # Each successful click moves on to the next prepared screen.
            self.texts = self.after_click[
                min(self.clicks - self.click_failures, len(self.after_click)) - 1
            ]

    def fill(self, control: Control, text: str) -> None:
        pass

    def screenshot(self, path: Path) -> None:
        raise RuntimeError("Target page, context or browser has been closed")

    def start_recording(self, path: Path) -> None:
        pass

    def stop_recording(self) -> None:
        pass

    def close(self) -> None:
        pass


class BrokenSurface(FlakySurface):
    """A surface that cannot even be looked at - the browser has gone."""

    def observe(self) -> Observation:
        raise RuntimeError("Browser has been closed")


class ClosedApp(FlakySurface):
    """An application that refuses every connection."""

    def open(self, url: str) -> None:
        self.opens += 1
        raise ConnectionError("net::ERR_CONNECTION_REFUSED at http://app/")


def flow(steps: list[Step], outcomes: list[OutcomeRule] | None = None) -> Capability:
    return Capability(
        name="demo",
        description="d",
        app=AppRef(product="p", base_url="http://app"),
        outputs=[
            OutputSpec(
                name="balance",
                type="money",
                description="d",
                extract=Extraction(kind="row_cell", row_contains="Share Savings", cell=2),
            )
        ],
        steps=steps,
        outcomes=outcomes or [],
        recorded=RecordMeta(recorded_at="2026-09-25T00:00:00Z"),
    )


def click(checkpoint: str = "", risky: bool = False) -> Step:
    return Step(
        id="s1",
        action="click",
        description="Press Search.",
        target=TargetSpec(role="button", name="Search"),
        checkpoint=Checkpoint(kind="text_present", value=checkpoint, timeout_ms=10)
        if checkpoint
        else None,
        risk="risky" if risky else "safe",
    )


OPEN = Step(id="s1", action="navigate", description="Open the app.", value="http://app")
EXTRACT = Step(id="s2", action="extract", description="Read the balance.")
INSTANT = Timing(target_wait_s=0, backoff_s=0, sleep=lambda _: None)


def test_a_click_that_errors_once_is_retried_and_the_run_succeeds() -> None:
    surface = FlakySurface(["Results"], click_failures=1)

    result = ReplayEngine(surface, flow([click()]), timing=INSTANT).run({})

    assert result.status == "success"
    assert surface.clicks == 2
    assert result.recoveries == ["retried_s1"]


def test_a_click_that_keeps_erroring_fails_with_the_error_not_a_traceback() -> None:
    surface = FlakySurface(["Results"], click_failures=99)

    result = ReplayEngine(surface, flow([click()]), timing=INSTANT).run({})

    assert result.status == "failure"
    assert result.failed_step == "s1"
    assert result.observed == "Locator.click: Timeout 10000ms exceeded."
    assert surface.clicks == 3


def test_an_irreversible_click_is_never_retried() -> None:
    """If it half-happened, a second attempt could do it twice."""
    surface = FlakySurface(["Results"], click_failures=99)
    capability = flow([click(risky=True)])
    engine = ReplayEngine(
        surface, capability, policy=Policy.for_capability(capability, risky="allow"), timing=INSTANT
    )
    result = engine.run({})

    assert result.status == "failure"
    assert surface.clicks == 1


def test_a_click_that_errored_after_it_landed_is_not_repeated() -> None:
    """The checkpoint already holds, so the step is done despite the error."""
    surface = FlakySurface(["Member Search Results"], click_failures=1)

    result = ReplayEngine(surface, flow([click("Results")]), timing=INSTANT).run({})

    assert result.status == "success"
    assert surface.clicks == 1


def test_a_control_that_appears_late_is_waited_for() -> None:
    clock = iter(range(1000))
    timing = Timing(target_wait_s=5, now=lambda: float(next(clock)), sleep=lambda _: None)
    surface = FlakySurface(["Results"], control_after=3)

    result = ReplayEngine(surface, flow([click()]), timing=timing).run({})

    assert result.status == "success"
    assert surface.clicks == 1


def test_an_application_that_is_down_is_a_failure_with_the_reason() -> None:
    surface = ClosedApp(["x"])

    result = ReplayEngine(surface, flow([OPEN]), timing=INSTANT).run({})

    assert result.status == "failure"
    assert "ERR_CONNECTION_REFUSED" in result.observed
    assert surface.opens == 3


def test_a_dead_browser_still_produces_a_result() -> None:
    result = ReplayEngine(BrokenSurface(["x"]), flow([click()]), timing=INSTANT).run({})

    assert result.status == "failure"
    assert "Browser has been closed" in result.message


def test_a_run_that_outlives_its_budget_stops() -> None:
    ticks = iter([0.0, 500.0, 500.0, 500.0])
    timing = Timing(run_budget_s=60, now=lambda: next(ticks), sleep=lambda _: None)
    surface = FlakySurface(["Results"])

    result = ReplayEngine(surface, flow([OPEN, click()]), timing=timing).run({})

    assert result.status == "failure"
    assert result.observed == "out of time"


BUSY = OutcomeRule(
    name="system_busy",
    when_text="System busy",
    kind="recoverable",
    message="Waiting for the host.",
    recovery=Recovery(kind="retry"),
)


def test_a_declared_retry_condition_that_clears_lets_the_run_finish() -> None:
    surface = FlakySurface(["Console"], after_click=[["System busy"], ["Results"]])

    result = ReplayEngine(surface, flow([click()], [BUSY]), timing=INSTANT).run({})

    assert result.status == "success"
    assert "system_busy" in result.recoveries


def test_a_declared_retry_condition_that_persists_is_reported_not_looped() -> None:
    surface = FlakySurface(["System busy"])

    result = ReplayEngine(surface, flow([click()], [BUSY]), timing=INSTANT).run({})

    assert result.status == "failure"
    assert result.observed == "system_busy persisted"
    assert surface.clicks == 3


def test_an_output_of_the_wrong_shape_is_a_failure_not_an_answer() -> None:
    """The wrong column, returned as if it were a balance, is the worst outcome."""
    surface = FlakySurface(["Share Savings\t0001\tSAVINGS"])

    result = ReplayEngine(surface, flow([OPEN, EXTRACT]), timing=INSTANT).run({})

    assert result.status == "failure"
    assert result.outputs == {}
    assert "not money" in result.observed
    assert "SAVINGS" not in result.observed


def test_a_well_shaped_output_is_returned() -> None:
    surface = FlakySurface(["Share Savings\t0001\t$4,182.55"])

    result = ReplayEngine(surface, flow([OPEN, EXTRACT]), timing=INSTANT).run({})

    assert result.status == "success"
    assert result.outputs == {"balance": "$4,182.55"}
