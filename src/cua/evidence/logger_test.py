import json
from pathlib import Path

from cua.evidence.logger import RunLog
from cua.policy.redact import MASK, Redactor


def read(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_record_keeps_its_own_kind_alongside_the_event_type(tmp_path: Path) -> None:
    """An intervention has a kind of its own; it must not cost the line its type."""
    log_path = tmp_path / "run.jsonl"
    with RunLog(log_path) as log:
        log.event("intervention", kind="hard_failure", step_id="s6")

    [record] = read(log_path)
    assert record["event"] == "intervention"
    assert record["kind"] == "hard_failure"


def test_secrets_are_masked_wherever_they_are_nested(tmp_path: Path) -> None:
    log_path = tmp_path / "run.jsonl"
    with RunLog(log_path, Redactor(secrets=frozenset({"hunter2"}))) as log:
        log.event("replay_started", inputs={"password": "hunter2"}, notes=["used hunter2"])

    [record] = read(log_path)
    assert record["inputs"] == {"password": MASK}
    assert record["notes"] == [f"used {MASK}"]


def test_every_line_is_timestamped(tmp_path: Path) -> None:
    log_path = tmp_path / "run.jsonl"
    with RunLog(log_path) as log:
        log.event("started")
    stamp = read(log_path)[0]["at"]
    assert isinstance(stamp, str)
    assert stamp.endswith("+00:00")
