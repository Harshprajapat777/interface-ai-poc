"""Engine paths that are hard to provoke against a live app.

A scripted surface makes restart-after-timeout and a vanished control
deterministic, which is what these two cases need to be worth testing.
"""

from pathlib import Path

from cua.artifact.schema import (
    AppRef,
    Capability,
    Checkpoint,
    OutcomeRule,
    RecordMeta,
    Recovery,
    Step,
    TargetSpec,
)
from cua.replay.engine import ReplayEngine
from cua.surface.base import Control, Observation


class ScriptedSurface:
    """A Surface that hands back prepared screens, advancing on each navigate."""

    def __init__(self, screens: list[Observation]) -> None:
        self._screens = screens
        self._opens = -1
        self.clicked: list[str] = []

    def open(self, url: str) -> None:
        self._opens += 1

    def observe(self) -> Observation:
        return self._screens[min(max(self._opens, 0), len(self._screens) - 1)]

    def click(self, control: Control) -> None:
        self.clicked.append(control.ref)

    def fill(self, control: Control, text: str) -> None:
        pass

    def screenshot(self, path: Path) -> None:
        pass

    def close(self) -> None:
        pass


def screen(*texts: str, controls: list[Control] | None = None) -> Observation:
    return Observation(url="http://app", title="t", controls=controls or [], texts=list(texts))


def capability(steps: list[Step], outcomes: list[OutcomeRule]) -> Capability:
    return Capability(
        name="demo",
        description="d",
        app=AppRef(product="p", base_url="http://app"),
        steps=steps,
        outcomes=outcomes,
        recorded=RecordMeta(recorded_at="2026-09-20T00:00:00Z"),
    )


NAVIGATE = Step(
    id="s1",
    action="navigate",
    description="Open the app.",
    value="http://app",
    checkpoint=Checkpoint(kind="text_present", value="Console", timeout_ms=10),
)

SESSION_EXPIRED = OutcomeRule(
    name="session_expired",
    when_text="session has expired",
    kind="recoverable",
    message="Signing on again.",
    recovery=Recovery(kind="restart"),
)


def test_a_timed_out_session_restarts_the_run() -> None:
    surface = ScriptedSurface([screen("session has expired"), screen("Console")])
    engine = ReplayEngine(surface, capability([NAVIGATE], [SESSION_EXPIRED]))

    result = engine.run({})

    assert result.status == "success"
    assert result.recoveries == ["session_expired"]


def test_restarting_forever_is_not_allowed() -> None:
    surface = ScriptedSurface([screen("session has expired")])
    engine = ReplayEngine(surface, capability([NAVIGATE], [SESSION_EXPIRED]))

    result = engine.run({})

    assert result.status == "failure"
    assert "Gave up" in result.message


def test_a_missing_control_is_a_debuggable_failure() -> None:
    click = Step(
        id="s2",
        action="click",
        description="Press Search.",
        target=TargetSpec(role="button", name="Search"),
    )
    surface = ScriptedSurface([screen("Console")])
    engine = ReplayEngine(surface, capability([click], []))

    result = engine.run({})

    assert result.status == "failure"
    assert result.failed_step == "s2"
    assert result.observed == "no matching control"
    assert "Search" in result.expected


def test_a_checkpoint_that_never_holds_reports_what_was_expected() -> None:
    surface = ScriptedSurface([screen("Some other page")])
    engine = ReplayEngine(surface, capability([NAVIGATE], []))

    result = engine.run({})

    assert result.status == "failure"
    assert result.failed_step == "s1"
    assert "Console" in result.expected


def test_navigating_off_the_allowlist_is_blocked_not_attempted() -> None:
    wander = Step(
        id="s1",
        action="navigate",
        description="Go somewhere else entirely.",
        value="http://evil.test/",
    )
    surface = ScriptedSurface([screen("Console")])
    engine = ReplayEngine(surface, capability([wander], []))

    result = engine.run({})

    assert result.status == "blocked"
    assert "host_not_allowed" in result.message


def test_a_risky_step_is_refused_by_default() -> None:
    post = Step(
        id="s1",
        action="click",
        description="Post the transfer.",
        target=TargetSpec(role="button", name="Confirm"),
        risk="risky",
    )
    surface = ScriptedSurface([screen("Console")])
    engine = ReplayEngine(surface, capability([post], []))

    result = engine.run({})

    assert result.status == "blocked"
    assert "risky_action_blocked" in result.message
    assert surface.clicked == []
