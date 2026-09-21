"""Replay a saved capability. This is the path an AI agent would trigger.

    uv run poe replay -- \
        --name get_savings_balance \
        --param operator_id=opr001 --param member_id=12345 \
        --secret password

No model is involved. Secret values come from CUA_SECRET_<NAME> in the
environment and never reach the artifact, the log or the console.
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from cua.artifact import store
from cua.cli.options import pairs
from cua.cli.options import secrets as read_secrets
from cua.escalation.broker import Escalation
from cua.escalation.operator import ConsoleOperator
from cua.evidence.logger import RunLog
from cua.policy.allowlist import Policy
from cua.policy.redact import Redactor
from cua.replay.engine import ReplayEngine
from cua.surface.browser import BrowserSurface

EVIDENCE = Path("evidence")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command line for a replay run."""
    parser = argparse.ArgumentParser(description="Replay a capability deterministically.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", type=int, default=None)
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--secret", action="append", default=[], metavar="NAME")
    parser.add_argument("--base-url", default=None, help="Run against another instance.")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--operator",
        action="store_true",
        help="Hand the live session to you when the run gets stuck.",
    )
    parser.add_argument(
        "--allow-risky",
        action="store_true",
        help="Let irreversible steps run once an operator has approved them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Replays one capability and prints a structured result."""
    load_dotenv()
    args = parse_args(argv)
    values = {**pairs(args.param), **read_secrets(args.secret)}

    capability = store.load(args.name, args.version)
    if args.base_url:
        capability = capability.model_copy(
            update={"app": capability.app.model_copy(update={"base_url": args.base_url})}
        )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = EVIDENCE / f"replay-{args.name}-{stamp}.jsonl"
    redactor = Redactor.for_values(values, capability.sensitive_names())

    headless = not (args.headed or args.operator)
    with RunLog(log_path, redactor) as log, BrowserSurface(headless=headless) as surface:
        log.event(
            "replay_started",
            capability=capability.name,
            version=capability.version,
            inputs=redactor.mapping(values),
        )
        # An operator can only take over a browser they can see, so asking for
        # one implies a headed run.
        escalation = (
            Escalation(ConsoleOperator(), evidence_dir=EVIDENCE) if args.operator else None
        )
        engine = ReplayEngine(
            surface,
            capability,
            evidence_dir=EVIDENCE,
            policy=Policy.for_capability(
                capability, risky="escalate" if args.allow_risky else "block"
            ),
            escalation=escalation,
        )
        result = engine.run(values)
        # Outputs go to the caller in full; the log only ever sees them redacted.
        log.event("replay_finished", **asdict(result))
        for request, resolution in escalation.history if escalation else []:
            log.event("handoff", **asdict(request), resolution=asdict(resolution))

    print(json.dumps(asdict(result), indent=2))
    print(f"Evidence: {log_path}", file=sys.stderr)
    return 0 if result.status in ("success", "business_outcome") else 1


if __name__ == "__main__":
    sys.exit(main())
