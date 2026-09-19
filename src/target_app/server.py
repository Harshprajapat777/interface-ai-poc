"""The fake servicing console: a stand-in for a legacy bank back-office app.

This is a test fixture, not product code. It exists so the agent has a realistic
surface to drive and so replay can be made to hit each runtime condition on
demand. Nothing here is real - the credentials are dummies and the member data
is invented.

Reachable conditions:

    12345          happy path, has a share savings balance
    99999          no such member          -> business outcome
    55555          restricted record       -> business outcome (permission denied)
    abc            non-numeric input       -> validation error
    77777          consent interstitial    -> recoverable
    ?chaos=slow    delayed response        -> recoverable (transient slowness)
    ?chaos=expire  forced session timeout  -> recoverable (re-authenticate)
    ?chaos=error   application error page  -> hard failure
"""

import os
import time

from flask import Blueprint, Flask, redirect, request, session
from flask.typing import ResponseReturnValue

from target_app import screens
from target_app.members import find_member

# Dummy credentials for the fixture. Not a secret, and not valid anywhere.
OPERATOR_ID = "opr001"
OPERATOR_PASSWORD = "test-password"

# How long a session survives without a request before the app signs you out.
IDLE_TIMEOUT_SECONDS = int(os.environ.get("TARGET_APP_IDLE_TIMEOUT", "900"))

SLOW_RESPONSE_SECONDS = 6.0

EXPIRED_MESSAGE = "Your session has expired. Please sign on again."

# Screens the operator can reach without being signed on.
PUBLIC_ENDPOINTS = frozenset({"console.index", "console.login", "console.nav"})

console = Blueprint("console", __name__)


def _session_is_live() -> bool:
    """True if the operator is signed on and has not been idle too long."""
    last_seen = session.get("last_seen")
    if not isinstance(last_seen, float):
        return False
    return (time.time() - last_seen) < IDLE_TIMEOUT_SECONDS


def _touch_session() -> None:
    """Pushes the idle deadline out, the way a real app does on each request."""
    session["last_seen"] = time.time()


def _apply_chaos() -> ResponseReturnValue | None:
    """Simulates the runtime condition asked for by ?chaos=, if any."""
    mode = request.args.get("chaos", "")
    if mode == "slow":
        time.sleep(SLOW_RESPONSE_SECONDS)
    elif mode == "expire":
        session.clear()
    elif mode == "error":
        return screens.error_screen(), 500
    return None


@console.before_request
def guard() -> ResponseReturnValue | None:
    """Applies chaos, then turns away anyone whose session is not live."""
    chaos_response = _apply_chaos()
    if chaos_response is not None:
        return chaos_response
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not _session_is_live():
        return screens.login_screen(EXPIRED_MESSAGE)
    _touch_session()
    return None


@console.get("/")
def index() -> ResponseReturnValue:
    """Sends signed-on operators to the console, everyone else to sign on."""
    if _session_is_live():
        return redirect("/console")
    return screens.login_screen()


@console.post("/login")
def login() -> ResponseReturnValue:
    """Checks the dummy credentials and starts a session."""
    operator = request.form.get("operator", "")
    password = request.form.get("password", "")
    if operator != OPERATOR_ID or password != OPERATOR_PASSWORD:
        return screens.login_screen("Invalid operator ID or password.")
    session["operator"] = operator
    _touch_session()
    return redirect("/console")


@console.get("/console")
def shell() -> ResponseReturnValue:
    """The frameset shell."""
    return screens.console_frameset()


@console.get("/nav")
def nav() -> ResponseReturnValue:
    """Left-hand menu frame."""
    return screens.nav_frame()


@console.get("/search")
def search_form() -> ResponseReturnValue:
    """The member lookup form, inside the content frame."""
    return screens.search_screen()


@console.post("/search")
def search() -> ResponseReturnValue:
    """Validates the member number and shows the one matching row, or an error."""
    number = request.form.get("member", "").strip()
    if not number.isdigit():
        return screens.search_screen("Member number must be numeric.")

    member = find_member(number)
    if member is None:
        return screens.search_screen("No member found for that number.")
    return screens.results_screen(member.number, member.name)


@console.get("/member/<number>")
def member_detail(number: str) -> ResponseReturnValue:
    """Member detail, gated on entitlement and on the consent interstitial."""
    member = find_member(number)
    if member is None:
        return screens.search_screen("No member found for that number.")
    if member.restricted:
        return screens.denied_screen()
    if member.needs_consent and request.args.get("consented") != "1":
        return screens.consent_screen(member.number)

    rows = [(a.kind, a.number, a.balance) for a in member.accounts]
    return screens.member_screen(member.number, member.name, rows)


def create_app() -> Flask:
    """Builds the Flask app with the console routes registered."""
    app = Flask(__name__)
    # Fixture-only signing key. A real app would load this from its environment.
    app.secret_key = "target-app-fixture-key"
    app.register_blueprint(console)
    return app


def main() -> None:
    """Runs the console for local use."""
    port = int(os.environ.get("TARGET_APP_PORT", "4173"))
    create_app().run(port=port, debug=False)


if __name__ == "__main__":
    main()
