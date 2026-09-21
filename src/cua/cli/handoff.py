"""Demonstrate the human handoff, reproducibly.

    uv run poe handoff --param operator_id=opr001 --param member_id=77777 --secret password

Member 77777 raises a consent interstitial, and this run deletes that rule from
the capability so replay genuinely does not recognise the screen. It stops, an
operator acts on the same live session, and the run finishes.

The operator here is scripted rather than a person at a keyboard, which is the
one thing in this path that is mocked. It is the same Operator protocol the
console implements, receiving the same request and driving the same session -
only the decision is pre-made. Use `poe replay --operator` to do it by hand.
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from cua.artifact import store
from cua.artifact.rebase import rebase
from cua.artifact.schema import Capability
from cua.cli.options import pairs
from cua.cli.options import secrets as read_secrets
from cua.escalation.broker import Escalation
from cua.escalation.operator import ScriptedOperator
from cua.evidence.logger import RunLog
from cua.policy.redact import Redactor
from cua.replay.engine import ReplayEngine
from cua.surface.base import Surface, Target, match_control
from cua.surface.browser import BrowserSurface

EVIDENCE = Path("evidence")


def forget_rule(capability: Capability, rule_name: str) -> Capability:
    """Removes a known condition so the run meets it unprepared."""
    outcomes = [rule for rule in capability.outcomes if rule.name != rule_name]
    return capability.model_copy(update={"outcomes": outcomes})


def dismiss_interstitial(live: Surface) -> None:
    """What an operator does: read the screen, click the button, hand back."""
    found = match_control(live.observe(), Target(role="button", name="Confirm Consent"))
    if found is None:
        raise LookupError("operator could not find the consent button")
    live.click(found[0])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command line for the handoff demonstration."""
    parser = argparse.ArgumentParser(description="Demonstrate a human handoff.")
    parser.add_argument("--name", default="get_savings_balance")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--secret", action="append", default=[], metavar="NAME")
    parser.add_argument("--forget", default="consent_required", help="Rule to un-know.")
    parser.add_argument("--headed", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Runs a replay that gets stuck, is unblocked by an operator, and finishes."""
    load_dotenv()
    args = parse_args(argv)
    values = {**pairs(args.param), **read_secrets(args.secret)}

    capability = store.load(args.name)
    if args.base_url:
        capability = rebase(capability, args.base_url)
    capability = forget_rule(capability, args.forget)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = EVIDENCE / f"handoff-{args.name}-{stamp}.jsonl"
    redactor = Redactor.for_values(values, capability.sensitive_names())

    with RunLog(log_path, redactor) as log, BrowserSurface(headless=not args.headed) as surface:
        escalation = Escalation(
            ScriptedOperator(dismiss_interstitial, note="confirmed verbal consent"),
            evidence_dir=EVIDENCE,
        )
        log.event("handoff_demo_started", capability=capability.name, forgot=args.forget)
        result = ReplayEngine(
            surface, capability, evidence_dir=EVIDENCE, escalation=escalation
        ).run(values)

        for request, resolution in escalation.history:
            log.event("intervention", **asdict(request))
            log.event("resolution", intervention=request.id, **asdict(resolution))
        log.event("handoff_demo_finished", **asdict(result))

    print(json.dumps(asdict(result), indent=2))
    print(f"Evidence: {log_path}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
