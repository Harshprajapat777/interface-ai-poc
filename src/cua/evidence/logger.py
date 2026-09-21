"""The run log: what happened, in order, with nothing sensitive in it.

One JSON object per line, because that is greppable, diffable and appendable
without holding a run in memory. Every value written passes through the
redactor first - the log is the most likely place for a credential or a member's
details to leak, precisely because nobody looks at it until something breaks.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from cua.policy.redact import Redactor


class RunLog:
    """Appends redacted events to a JSONL file."""

    def __init__(self, path: Path, redactor: Redactor | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.redactor = redactor or Redactor()
        self._file = path.open("a", encoding="utf-8")

    def event(self, name: str, /, **fields: Any) -> None:
        """Writes one event, redacting every string it carries.

        The event's own type is written as `event`, not `kind`, because the
        records being logged have a `kind` of their own - an intervention's
        kind is why it was raised - and splatting one over the other silently
        cost the log line its type.
        """
        record = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "event": name,
            **{key: self._clean(value) for key, value in fields.items()},
        }
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Closes the file."""
        self._file.close()

    def __enter__(self) -> "RunLog":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _clean(self, value: Any) -> Any:
        """Redacts strings anywhere in a value, leaving structure intact."""
        if isinstance(value, str):
            return self.redactor.text(value)
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        return value
