"""Replay regression matrix: every runtime condition, N times, checked against its answer.

Needs the target app running (`uv run poe app`) and CUA_SECRET_PASSWORD set.

    uv run poe regression          # 5 runs per scenario
    uv run poe regression 20       # a stronger stability signal

Exits non-zero if any replay disagrees with its expected result, so it can gate
a change the same way the unit tests do.
"""

import json
import subprocess
import sys
import time
from collections import Counter

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
BASE = "http://127.0.0.1:4173"

# name, member_id, base_url or None, expected status, expected balance or None
SCENARIOS = [
    ("happy path", "12345", None, "success", "$4,182.55"),
    ("member not found", "99999", None, "business_outcome", None),
    ("access denied", "55555", None, "business_outcome", None),
    ("consent pop-up", "77777", None, "success", "$27.03"),
    ("bad input", "12ab!", None, "rejected", None),
    ("slow app", "12345", f"{BASE}/?chaos=slow", "success", "$4,182.55"),
    ("session expired", "12345", f"{BASE}/?chaos=expire", "success", "$4,182.55"),
    ("app crash", "12345", f"{BASE}/?chaos=error", "failure", None),
]


def replay(member: str, base_url: str | None) -> dict[str, object]:
    """Runs one replay through the CLI, exactly as a caller would."""
    cmd = [
        sys.executable, "-m", "cua.cli.replay",
        "--name", "get_savings_balance", "--version", "1",
        "--param", "operator_id=opr001", "--param", f"member_id={member}",
        "--secret", "password",
    ]  # fmt: skip
    if base_url:
        cmd += ["--base-url", base_url]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout
    start = out.find("{")
    if start < 0:
        return {"status": "CRASH", "message": out[-300:]}
    parsed: dict[str, object] = json.loads(out[start:])
    return parsed


def check(scenario: tuple[str, str, str | None, str, str | None]) -> int:
    """Runs one scenario RUNS times, prints a line, and returns how many matched."""
    name, member, base_url, want_status, want_balance = scenario
    passed, times, seen, detail = 0, [], Counter[str](), ""
    for _ in range(RUNS):
        started = time.perf_counter()
        result = replay(member, base_url)
        times.append(time.perf_counter() - started)
        outputs = result.get("outputs") or {}
        balance = outputs.get("share_savings_balance") if isinstance(outputs, dict) else None
        status = str(result["status"])
        passed += status == want_status and (want_balance is None or balance == want_balance)
        seen[status] += 1
        detail = str(result.get("outcome") or result.get("message", ""))
    verdict = "PASS" if passed == RUNS else "FAIL"
    average = sum(times) / len(times)
    print(f"{verdict}  {name:18} {passed}/{RUNS}  avg {average:.1f}s  {dict(seen)}  [{detail}]")
    return passed


def main() -> int:
    total = sum(check(scenario) for scenario in SCENARIOS)
    print(f"\nTOTAL {total}/{RUNS * len(SCENARIOS)} replays matched the expected result")
    return 0 if total == RUNS * len(SCENARIOS) else 1


if __name__ == "__main__":
    sys.exit(main())
