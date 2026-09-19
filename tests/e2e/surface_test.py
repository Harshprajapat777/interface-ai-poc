"""Drives the real target app in a real browser.

This is the test that proves the seam works on the hostile surface: the
controls that matter live inside a frameset, and half of them have no
accessible name.
"""

import threading
from collections.abc import Iterator

import pytest
from werkzeug.serving import make_server

from cua.surface.base import Observation, Target, match_control
from cua.surface.browser import BrowserSurface
from target_app.server import OPERATOR_ID, OPERATOR_PASSWORD, create_app

HOST = "127.0.0.1"
PORT = 4199
BASE_URL = f"http://{HOST}:{PORT}"


@pytest.fixture(scope="module")
def app_server() -> Iterator[None]:
    """Runs the target app in a background thread for the duration of the module."""
    server = make_server(HOST, PORT, create_app())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield
    server.shutdown()


@pytest.fixture(scope="module")
def surface(app_server: None) -> Iterator[BrowserSurface]:
    with BrowserSurface(headless=True) as browser:
        yield browser


def act(
    surface: BrowserSurface,
    seen: Observation,
    target: Target,
    text: str | None = None,
) -> None:
    """Finds a control in the snapshot and either clicks it or types into it."""
    found = match_control(seen, target)
    assert found is not None, f"could not find {target}"
    if text is None:
        surface.click(found[0])
    else:
        surface.fill(found[0], text)


def sign_on(surface: BrowserSurface) -> None:
    """Signs on if needed. The browser is shared, so a session may already be live."""
    surface.open(BASE_URL)
    seen = surface.observe()
    if match_control(seen, Target(role="textbox", label="Operator ID")) is None:
        return
    act(surface, seen, Target(role="textbox", label="Operator ID"), OPERATOR_ID)
    act(surface, seen, Target(role="textbox", label="Password"), OPERATOR_PASSWORD)
    act(surface, seen, Target(role="button", name="Sign On"))


def test_login_inputs_are_nameless_but_still_reachable(surface: BrowserSurface) -> None:
    surface.open(BASE_URL)
    seen = surface.observe()
    boxes = [c for c in seen.controls if c.role == "textbox"]
    assert [c.name for c in boxes] == ["", ""]
    assert [c.label for c in boxes] == ["Operator ID", "Password"]


def test_snapshot_reaches_inside_the_frameset(surface: BrowserSurface) -> None:
    sign_on(surface)
    seen = surface.observe()
    frames = {c.frame for c in seen.controls}
    assert "contentframe" in frames
    assert "navframe" in frames


def test_search_box_is_found_by_its_accessible_name(surface: BrowserSurface) -> None:
    sign_on(surface)
    seen = surface.observe()
    found = match_control(seen, Target(role="textbox", name="Member Number"))
    assert found is not None
    assert found[1] == "name"


def test_full_lookup_reads_the_savings_balance(surface: BrowserSurface) -> None:
    sign_on(surface)
    seen = surface.observe()
    act(surface, seen, Target(role="textbox", name="Member Number", frame="contentframe"), "12345")
    act(surface, seen, Target(role="button", name="Search", frame="contentframe"))

    seen = surface.observe()
    act(surface, seen, Target(role="button", name="Open", frame="contentframe"))

    seen = surface.observe()
    # Table rows arrive as one line with tab-separated cells, so match inside the row.
    assert any("Share Savings" in text and "$4,182.55" in text for text in seen.texts)


def test_unknown_member_shows_a_business_outcome(surface: BrowserSurface) -> None:
    sign_on(surface)
    seen = surface.observe()
    act(surface, seen, Target(role="textbox", name="Member Number", frame="contentframe"), "99999")
    act(surface, seen, Target(role="button", name="Search", frame="contentframe"))

    seen = surface.observe()
    assert any("No member found" in text for text in seen.texts)
