from pathlib import Path

import pytest

from cua.escalation.broker import (
    ControlTransfer,
    Escalation,
    Intervention,
    Owner,
    Resolution,
)
from cua.escalation.operator import ConsoleOperator, ScriptedOperator
from cua.surface.base import Control, Observation, Surface


class FakeSurface:
    """A surface whose screen the operator can change, like a real one."""

    def __init__(self, texts: list[str], url: str = "http://app/step") -> None:
        self.texts = texts
        self.url = url
        self.recording: Path | None = None
        self.stopped = False

    def open(self, url: str) -> None:
        self.url = url

    def observe(self) -> Observation:
        return Observation(url=self.url, title="t", controls=[], texts=list(self.texts))

    def click(self, control: Control) -> None:
        pass

    def fill(self, control: Control, text: str) -> None:
        pass

    def screenshot(self, path: Path) -> None:
        pass

    def start_recording(self, path: Path) -> None:
        self.recording = path

    def stop_recording(self) -> None:
        self.stopped = True

    def close(self) -> None:
        pass


def intervention() -> Intervention:
    return Intervention.raise_for(
        capability="get_savings_balance",
        goal="Read a member's savings balance",
        step_id="s6",
        kind="hard_failure",
        reason="The Open button is not on the results screen.",
        observation=Observation(url="http://app/step", title="t", controls=[], texts=["Results"]),
    )


def test_only_one_party_holds_the_session_at_a_time() -> None:
    transfer = ControlTransfer()
    assert transfer.current() is Owner.AUTOMATION

    with transfer.to_operator():
        assert transfer.current() is Owner.OPERATOR
        with pytest.raises(PermissionError, match="automation tried to act"):
            transfer.check(Owner.AUTOMATION)

    assert transfer.current() is Owner.AUTOMATION
    transfer.check(Owner.AUTOMATION)


def test_control_comes_back_even_if_the_operator_fails() -> None:
    """A session nobody owns is the one state the run cannot recover from."""
    transfer = ControlTransfer()
    with pytest.raises(RuntimeError), transfer.to_operator():
        raise RuntimeError("operator console crashed")
    assert transfer.current() is Owner.AUTOMATION


def test_the_operator_works_the_same_live_session() -> None:
    surface = FakeSurface(["Search results"])

    def fix(live: Surface) -> None:
        live.open("http://app/member/12345")

    escalation = Escalation(ScriptedOperator(fix, note="opened the record by hand"))
    resolution = escalation.request(intervention(), surface)

    assert resolution.outcome == "resumed"
    assert resolution.url_before == "http://app/step"
    assert resolution.url_after == "http://app/member/12345"


def test_what_the_human_changed_is_recorded() -> None:
    surface = FakeSurface(["Search results"])

    def fix(live: Surface) -> None:
        surface.texts = ["Search results", "Current Balance", "Share Savings"]

    escalation = Escalation(ScriptedOperator(fix))
    resolution = escalation.request(intervention(), surface)

    assert resolution.changes == ["Current Balance", "Share Savings"]


def test_the_session_is_recorded_when_evidence_is_being_kept(tmp_path: Path) -> None:
    surface = FakeSurface(["Search results"])
    escalation = Escalation(ScriptedOperator(lambda _: None), evidence_dir=tmp_path)

    resolution = escalation.request(intervention(), surface)

    assert surface.recording is not None
    assert surface.stopped
    assert resolution.trace.endswith(".zip")


def test_an_abandoned_handoff_says_so() -> None:
    escalation = Escalation(ScriptedOperator(lambda _: None, outcome="abandoned"))
    resolution = escalation.request(intervention(), FakeSurface(["Results"]))
    assert resolution.outcome == "abandoned"


def test_every_handoff_is_kept_for_the_audit_trail() -> None:
    escalation = Escalation(ScriptedOperator(lambda _: None))
    escalation.request(intervention(), FakeSurface(["Results"]))
    escalation.request(intervention(), FakeSurface(["Results"]))
    assert len(escalation.history) == 2


def test_the_console_shows_an_operator_enough_to_act_on() -> None:
    shown: list[str] = []
    answers = iter(["done", "clicked Open manually"])
    operator = ConsoleOperator(prompt=lambda text: next(answers))

    request = intervention()
    resolution = operator.take_over(request, FakeSurface(["Results"]))

    assert resolution.outcome == "resumed"
    assert resolution.note == "clicked Open manually"
    assert shown == []


def test_the_console_treats_abandon_as_abandon() -> None:
    answers = iter(["abandon", "cannot proceed, record is locked"])
    operator = ConsoleOperator(prompt=lambda text: next(answers))
    resolution = operator.take_over(intervention(), FakeSurface(["Results"]))
    assert resolution.outcome == "abandoned"


def test_a_request_carries_enough_context_to_pick_up_cold() -> None:
    request = intervention()
    assert request.capability == "get_savings_balance"
    assert request.step_id == "s6"
    assert request.kind == "hard_failure"
    assert "Open button" in request.reason
    assert request.screen == ["Results"]
    assert request.id and request.raised_at


def test_resolution_defaults_are_empty_not_missing() -> None:
    assert Resolution(outcome="resumed").changes == []
