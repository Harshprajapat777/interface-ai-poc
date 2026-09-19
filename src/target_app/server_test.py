"""Each test pins one runtime condition the replay engine will have to handle."""

from collections.abc import Iterator

import pytest
from flask.testing import FlaskClient

from target_app.server import OPERATOR_ID, OPERATOR_PASSWORD, create_app


@pytest.fixture
def client() -> Iterator[FlaskClient]:
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def sign_on(client: FlaskClient) -> None:
    """Signs the test client on with the fixture credentials."""
    client.post(
        "/login",
        data={"operator": OPERATOR_ID, "password": OPERATOR_PASSWORD},
        follow_redirects=True,
    )


def body(client: FlaskClient, url: str) -> str:
    """Fetches a URL and returns the page text."""
    return client.get(url).get_data(as_text=True)


def test_sign_on_is_required_before_the_console() -> None:
    app = create_app()
    with app.test_client() as client:
        assert "Sign On" in body(client, "/")


def test_wrong_password_is_rejected(client: FlaskClient) -> None:
    response = client.post("/login", data={"operator": OPERATOR_ID, "password": "wrong"})
    assert "Invalid operator ID or password" in response.get_data(as_text=True)


def test_console_is_a_frameset(client: FlaskClient) -> None:
    sign_on(client)
    assert "<frameset" in body(client, "/console")


def test_happy_path_member_has_a_savings_balance(client: FlaskClient) -> None:
    sign_on(client)
    assert "$4,182.55" in body(client, "/member/12345")


def test_unknown_member_is_a_business_outcome(client: FlaskClient) -> None:
    sign_on(client)
    response = client.post("/search", data={"member": "99999"})
    assert "No member found" in response.get_data(as_text=True)


def test_non_numeric_member_is_a_validation_error(client: FlaskClient) -> None:
    sign_on(client)
    response = client.post("/search", data={"member": "abc"})
    assert "must be numeric" in response.get_data(as_text=True)


def test_restricted_member_is_denied(client: FlaskClient) -> None:
    sign_on(client)
    assert "Access denied" in body(client, "/member/55555")


def test_consent_interstitial_blocks_then_clears(client: FlaskClient) -> None:
    sign_on(client)
    assert "Consent confirmation required" in body(client, "/member/77777")
    assert "$27.03" in body(client, "/member/77777?consented=1")


def test_chaos_expire_ends_the_session(client: FlaskClient) -> None:
    sign_on(client)
    assert "session has expired" in body(client, "/search?chaos=expire")


def test_chaos_error_returns_the_application_error_page(client: FlaskClient) -> None:
    sign_on(client)
    response = client.get("/search?chaos=error")
    assert response.status_code == 500
    assert "Application error" in response.get_data(as_text=True)


def test_the_markup_is_hostile(client: FlaskClient) -> None:
    """No test IDs, and ids are positional rather than meaningful."""
    sign_on(client)
    page = body(client, "/search")
    assert "data-testid" not in page
    assert "ctl00_r1_c2" in page
