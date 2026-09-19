"""Run a discovery: let the model work out a flow, then save it as a capability.

    uv run poe discover -- \
        --goal "Look up member 12345 and read their current savings balance" \
        --name get_savings_balance \
        --url http://127.0.0.1:4173 \
        --param operator_id=opr001 --param member_id=12345 \
        --secret password

Secret values are read from the environment as CUA_SECRET_<NAME> and are never
passed to the model, never printed and never written to the artifact.
"""

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from cua.artifact import store
from cua.cli.options import pairs
from cua.cli.options import secrets as read_secrets
from cua.discovery.agent import DiscoveryAgent, DiscoveryRun, RecordedAction
from cua.discovery.recorder import record
from cua.evidence.logger import RunLog
from cua.policy.allowlist import Allowlist, Policy
from cua.policy.redact import Redactor
from cua.surface.browser import BrowserSurface

EVIDENCE = Path("evidence")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command line for a discovery run."""
    parser = argparse.ArgumentParser(description="Discover a flow with an LLM.")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--name", required=True, help="Capability name, e.g. get_savings_balance")
    parser.add_argument("--url", required=True, help="Where the flow starts.")
    parser.add_argument("--product", default="northgate-servicing-console")
    parser.add_argument("--tenant", default="demo-cu")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--secret", action="append", default=[], metavar="NAME")
    parser.add_argument("--model", default=None)
    parser.add_argument("--headed", action="store_true", help="Watch the browser work.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Runs discovery and saves the resulting capability."""
    load_dotenv()
    args = parse_args(argv)
    values = pairs(args.param)
    secrets = read_secrets(args.secret)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = EVIDENCE / f"discovery-{args.name}-{stamp}.jsonl"
    redactor = Redactor(secrets=frozenset(secrets.values()))

    policy = Policy(allowlist=_allowlist(args.url))
    model = args.model or os.environ.get("ANTHROPIC_MODEL") or None

    with RunLog(log_path, redactor) as log, BrowserSurface(headless=not args.headed) as surface:
        log.event("discovery_started", goal=args.goal, url=args.url, params=values)
        surface.open(args.url)

        agent = _agent(surface, policy, model)
        run = agent.run(args.goal, values, secrets)
        _log_actions(log, run)

        if not run.succeeded:
            log.event("discovery_failed", turns=run.llm_turns)
            surface.screenshot(EVIDENCE / f"discovery-{args.name}-{stamp}-failed.png")
            print(f"Goal not reached after {run.llm_turns} turns. See {log_path}")
            return 1

        surface.screenshot(EVIDENCE / f"discovery-{args.name}-{stamp}-final.png")
        capability = record(
            run,
            name=args.name,
            product=args.product,
            base_url=args.url,
            values=values,
            secrets=sorted(secrets),
            version=store.next_version(args.name),
            tenant=args.tenant,
        )
        path = store.save(capability)
        log.event("artifact_saved", path=str(path), steps=len(capability.steps))
        print(f"Saved {path} ({len(capability.steps)} steps, {run.llm_turns} model turns)")
        print(f"Evidence: {log_path}")
    return 0


def _allowlist(url: str) -> Allowlist:
    """Permits only the host the run was pointed at."""
    return Allowlist(hosts=frozenset({urlparse(url).netloc}))


def _agent(surface: BrowserSurface, policy: Policy, model: str | None) -> DiscoveryAgent:
    """Builds the agent, letting the default model stand unless one was asked for."""
    if model:
        return DiscoveryAgent(surface, policy, model=model)
    return DiscoveryAgent(surface, policy)


def _log_actions(log: RunLog, run: DiscoveryRun) -> None:
    """Writes one event per action the model took."""
    for index, action in enumerate(run.actions, start=1):
        log.event(
            "action",
            step=index,
            action=action.action,
            why=action.why,
            expected=action.expect_text,
            checkpoint_held=action.checkpoint_held,
            control=_describe(action),
        )
    for output in run.outputs:
        log.event("output_declared", name=output.name, row=output.row_contains, cell=output.cell)


def _describe(action: RecordedAction) -> str:
    """A short description of the control an action touched."""
    control = action.control
    if control is None:
        return ""
    return f"{control.role} name={control.name!r} label={control.label!r} frame={control.frame!r}"


if __name__ == "__main__":
    sys.exit(main())
