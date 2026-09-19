"""Replays the hand-written artifact against the real app in a real browser.

Every case here is a runtime condition the brief asks replay to tell apart:
a success, two business outcomes, a recoverable interstitial, a caller error
caught before the browser opens, and a hard application failure.
"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from cua.artifact import store
from cua.artifact.schema import Capability
from cua.replay.engine import ReplayEngine
from cua.surface.browser import BrowserSurface
from target_app.server import OPERATOR_ID, OPERATOR_PASSWORD, create_app

HOST = "127.0.0.1"
PORT = 4198
BASE_URL = f"http://{HOST}:{PORT}"
FIXTURE = Path("tests/fixtures/get_savings_balance_handwritten.json")

RECORDED_URL = "http://127.0.0.1:4173"


def retarget(capability: Capability, base_url: str) -> Capability:
    """Points a recorded artifact at a different instance of the same app.

    Rebasing rather than re-recording is the small version of the multi-tenant
    story: the same steps, a different institution's URL.
    """
    steps = [
        step.model_copy(update={"value": step.value.replace(RECORDED_URL, base_url)})
        if step.value and RECORDED_URL in step.value
        else step
        for step in capability.steps
    ]
    app = capability.app.model_copy(update={"base_url": base_url})
    return capability.model_copy(update={"steps": steps, "app": app})


@pytest.fixture(scope="module")
def app_server() -> Iterator[None]:
    server = make_server(HOST, PORT, create_app())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield
    server.shutdown()


@pytest.fixture(scope="module")
def capability() -> Capability:
    return retarget(store.load_file(FIXTURE), BASE_URL)


@pytest.fixture
def engine(app_server: None, capability: Capability) -> Iterator[ReplayEngine]:
    with BrowserSurface(headless=True) as surface:
        yield ReplayEngine(surface, capability)


def inputs(member_id: str) -> dict[str, str]:
    return {
        "operator_id": OPERATOR_ID,
        "password": OPERATOR_PASSWORD,
        "member_id": member_id,
    }


def test_a_good_lookup_returns_the_declared_outputs(engine: ReplayEngine) -> None:
    result = engine.run(inputs("12345"))

    assert result.status == "success", result
    assert result.outputs["savings_balance"] == "$4,182.55"
    assert result.outputs["member_name"] == "DELACROIX, MARGUERITE"
    assert result.steps_run == 8


def test_an_unknown_member_is_a_business_outcome_not_a_failure(engine: ReplayEngine) -> None:
    result = engine.run(inputs("99999"))

    assert result.status == "business_outcome"
    assert result.outcome == "member_not_found"
    assert result.outputs == {}


def test_a_restricted_member_is_a_business_outcome(engine: ReplayEngine) -> None:
    result = engine.run(inputs("55555"))

    assert result.status == "business_outcome"
    assert result.outcome == "access_denied"


def test_an_unexpected_interstitial_is_recovered_and_the_run_completes(
    engine: ReplayEngine,
) -> None:
    result = engine.run(inputs("77777"))

    assert result.status == "success", result
    assert result.recoveries == ["consent_required"]
    assert result.outputs["savings_balance"] == "$27.03"


def test_a_malformed_input_is_rejected_before_the_browser_is_touched(
    engine: ReplayEngine,
) -> None:
    result = engine.run(inputs("abc"))

    assert result.status == "rejected"
    assert "member_id" in result.message


def test_an_application_error_is_a_hard_failure(
    app_server: None,
    capability: Capability,
) -> None:
    # Send the very first navigate at the app's error page.
    first = capability.steps[0].model_copy(update={"value": f"{BASE_URL}/?chaos=error"})
    broken = capability.model_copy(update={"steps": [first, *capability.steps[1:]]})
    with BrowserSurface(headless=True) as surface:
        result = ReplayEngine(surface, broken).run(inputs("12345"))

    assert result.status == "failure"
    assert "application error" in result.message.lower()
