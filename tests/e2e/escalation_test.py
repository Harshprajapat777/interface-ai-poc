"""A real handoff on a real browser session.

The setup is the honest version of "the automation hit something it does not
know how to handle": member 77777 raises a consent interstitial, and the
capability under test has had that rule removed, so replay genuinely does not
know what the screen is. It stops, a person clicks the button on the same live
session, and the run finishes.
"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from cua.artifact import store
from cua.artifact.schema import Capability
from cua.escalation.broker import Escalation, Owner
from cua.escalation.operator import ScriptedOperator
from cua.replay.engine import ReplayEngine
from cua.surface.base import Surface, Target, match_control
from cua.surface.browser import BrowserSurface
from target_app.server import OPERATOR_ID, OPERATOR_PASSWORD, create_app

HOST = "127.0.0.1"
PORT = 4197
BASE_URL = f"http://{HOST}:{PORT}"
ARTIFACT = Path("artifacts/get_savings_balance/v1.json")


@pytest.fixture(scope="module")
def app_server() -> Iterator[None]:
    server = make_server(HOST, PORT, create_app())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield
    server.shutdown()


@pytest.fixture
def unaware(app_server: None) -> Capability:
    """The capability with the consent rule removed, so the popup is a surprise."""
    capability = store.load_file(ARTIFACT)
    steps = [
        step.model_copy(update={"value": BASE_URL}) if step.action == "navigate" else step
        for step in capability.steps
    ]
    outcomes = [rule for rule in capability.outcomes if rule.name != "consent_required"]
    return capability.model_copy(
        update={
            "steps": steps,
            "outcomes": outcomes,
            "app": capability.app.model_copy(update={"base_url": BASE_URL}),
        }
    )


def inputs(member_id: str) -> dict[str, str]:
    return {
        "operator_id": OPERATOR_ID,
        "password": OPERATOR_PASSWORD,
        "member_id": member_id,
    }


def dismiss_the_interstitial(live: Surface) -> None:
    """What a human operator would do: read the screen and click the button."""
    found = match_control(live.observe(), Target(role="button", name="Confirm Consent"))
    assert found is not None, "operator could not find the consent button"
    live.click(found[0])


def test_a_person_unblocks_the_run_and_it_finishes(unaware: Capability) -> None:
    escalation = Escalation(
        ScriptedOperator(dismiss_the_interstitial, note="confirmed verbal consent")
    )
    with BrowserSurface(headless=True) as surface:
        engine = ReplayEngine(surface, unaware, escalation=escalation)
        result = engine.run(inputs("77777"))

    assert result.status == "success", result
    assert result.outputs["share_savings_balance"] == "$27.03"
    assert len(result.interventions) == 1

    request, resolution = escalation.history[0]
    assert request.kind == "hard_failure"
    assert "Consent confirmation required" in "\n".join(request.screen)
    assert resolution.outcome == "resumed"
    assert resolution.note == "confirmed verbal consent"
    # The operator worked the same session: the member detail they reached is
    # on screen afterwards, and the run never signed on a second time.
    assert any("Share Savings" in line for line in resolution.changes)
    # The page URL does not move, because on a frameset the navigation happens
    # inside a frame. That is precisely why the screen diff, not the address,
    # is what tells us what the human did.
    assert resolution.url_before == resolution.url_after


def test_control_is_handed_back_after_the_person_is_done(unaware: Capability) -> None:
    escalation = Escalation(ScriptedOperator(dismiss_the_interstitial))
    with BrowserSurface(headless=True) as surface:
        ReplayEngine(surface, unaware, escalation=escalation).run(inputs("77777"))

    assert escalation.transfer.current() is Owner.AUTOMATION


def test_without_an_operator_the_same_run_simply_fails(unaware: Capability) -> None:
    """The handoff is what changes the outcome, not anything else in the setup."""
    with BrowserSurface(headless=True) as surface:
        result = ReplayEngine(surface, unaware).run(inputs("77777"))

    assert result.status == "failure"
    assert result.outputs == {}
