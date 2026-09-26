"""Discovery's guardrails, exercised without a model or a browser.

The agent's tool handlers are driven directly, as the model would call them,
against a scripted screen. What is under test is what the agent refuses and
what it lets into the recording - not the model's judgement.
"""

from pathlib import Path
from typing import Any

import httpx2
from anthropic import APIConnectionError

from cua.discovery.agent import DiscoveryAgent, DiscoveryRun
from cua.policy.allowlist import ALL_ACTIONS, Allowlist, Policy
from cua.surface.base import Control, Observation

HOME = "http://app/console"


def button(ref: str, name: str) -> Control:
    return Control(ref=ref, role="button", name=name, label="", frame="", index=0)


class Screen:
    """A surface whose clicks lead wherever the test says they lead."""

    def __init__(self, controls: list[Control], texts: list[str], leads_to: str = HOME) -> None:
        self.controls = controls
        self.texts = texts
        self.url = HOME
        self.leads_to = leads_to
        self.clicked: list[str] = []
        self.opened: list[str] = []

    def open(self, url: str) -> None:
        self.opened.append(url)
        self.url = url

    def observe(self) -> Observation:
        return Observation(url=self.url, title="t", controls=self.controls, texts=self.texts)

    def click(self, control: Control) -> None:
        self.clicked.append(control.ref)
        self.url = self.leads_to
        self.texts = [*self.texts, "Done"]

    def fill(self, control: Control, text: str) -> None:
        pass

    def screenshot(self, path: Path) -> None:
        pass

    def start_recording(self, path: Path) -> None:
        pass

    def stop_recording(self) -> None:
        pass

    def close(self) -> None:
        pass


class Unreachable:
    """A model API that is down, after the SDK's own retries are spent."""

    class messages:  # noqa: N801 - mirrors the SDK's attribute
        @staticmethod
        def create(**_: Any) -> Any:
            raise APIConnectionError(request=httpx2.Request("POST", "https://api.test"))


def agent(screen: Screen, risky: str = "block", actions: frozenset[str] = ALL_ACTIONS) -> Any:
    policy = Policy(allowlist=Allowlist(hosts=frozenset({"app"}), actions=actions), risky=risky)
    return DiscoveryAgent(screen, policy, client=Unreachable())  # type: ignore[arg-type]


def click(ref: str) -> dict[str, str]:
    return {"ref": ref, "why": "w", "expect_text": "Done"}


def test_an_unreachable_model_ends_the_run_with_the_reason() -> None:
    run = agent(Screen([], ["Console"])).run("goal", {}, {})

    assert not run.succeeded
    assert "model API" in run.error


def test_an_irreversible_click_is_refused_by_default() -> None:
    screen = Screen([button("c1", "Transfer Funds")], ["Console"])
    run = DiscoveryRun(goal="g", model="m")

    reply = agent(screen)._perform("click", click("c1"), run, {})

    assert reply.startswith("Refused")
    assert screen.clicked == []
    assert run.actions == []


def test_an_allowed_irreversible_click_is_recorded_as_risky() -> None:
    screen = Screen([button("c1", "Transfer Funds")], ["Console"])
    run = DiscoveryRun(goal="g", model="m")

    agent(screen, risky="escalate")._perform("click", click("c1"), run, {})

    assert screen.clicked == ["c1"]
    assert run.actions[0].risky


def test_a_click_that_leaves_the_application_is_undone_and_not_recorded() -> None:
    screen = Screen([button("c1", "Help")], ["Console"], leads_to="http://elsewhere.test/")
    run = DiscoveryRun(goal="g", model="m")

    reply = agent(screen)._perform("click", click("c1"), run, {})

    assert "left the application" in reply
    assert screen.opened == [HOME]
    assert run.actions == []


def test_an_action_type_outside_the_allowlist_is_refused() -> None:
    screen = Screen([button("c1", "Search")], ["Console"])
    run = DiscoveryRun(goal="g", model="m")

    reply = agent(screen, actions=frozenset({"navigate"}))._perform("click", click("c1"), run, {})

    assert "action_not_allowed" in reply
    assert screen.clicked == []


def test_an_output_that_is_not_on_screen_is_not_recorded() -> None:
    screen = Screen([], ["Share Savings\t0001\t$4,182.55"])
    run = DiscoveryRun(goal="g", model="m")
    declared = {"name": "balance", "description": "d", "row_contains": "Checking", "cell": 2}

    reply = agent(screen)._perform("record_output", declared, run, {})

    assert reply.startswith("Nothing to read")
    assert run.outputs == []


def test_an_output_is_typed_from_the_value_actually_seen() -> None:
    screen = Screen([], ["Share Savings\t0001\t$4,182.55"])
    run = DiscoveryRun(goal="g", model="m")
    declared = {"name": "balance", "description": "d", "row_contains": "Share Savings", "cell": 2}

    agent(screen)._perform("record_output", declared, run, {})

    assert run.outputs[0].type == "money"
