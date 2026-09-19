"""Keeping regulated data out of anything we persist.

Two rules, and the difference between them matters:

- Secrets - passwords, tokens - are never written anywhere, ever.
- PII is returned to the caller, because the caller asked for it, but is
  redacted on its way into a log or an artifact. A run that looked up a
  member's balance should leave no record of whose balance it was.

Redaction is applied at the boundary where data is written out, not at the
boundary where it is read. Scrubbing on the way in would mean the engine could
not compare a value it just typed against what the screen shows.
"""

import re
from dataclasses import dataclass

MASK = "[redacted]"

# Shapes worth catching even when nobody declared them sensitive, because a
# log line is forever and a developer forgetting one flag should not be fatal.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
)


@dataclass(frozen=True, slots=True)
class Redactor:
    """Removes known secret values and obvious PII shapes from text."""

    secrets: frozenset[str] = frozenset()

    @staticmethod
    def for_values(values: dict[str, str], sensitive: set[str]) -> "Redactor":
        """Builds a redactor from the sensitive inputs of one call."""
        found = {values[name] for name in sensitive if values.get(name)}
        return Redactor(secrets=frozenset(found))

    def text(self, value: str) -> str:
        """Returns the text with secrets and recognised PII masked."""
        cleaned = value
        # Longest first, so a secret containing another is not partly revealed.
        for secret in sorted(self.secrets, key=len, reverse=True):
            cleaned = cleaned.replace(secret, MASK)
        for _, pattern in PATTERNS:
            cleaned = pattern.sub(MASK, cleaned)
        return cleaned

    def mapping(self, values: dict[str, str]) -> dict[str, str]:
        """Redacts every value in a dictionary, leaving the keys readable."""
        return {key: self.text(value) for key, value in values.items()}

    def lines(self, values: list[str]) -> list[str]:
        """Redacts a list of text lines."""
        return [self.text(line) for line in values]


def mask_all(values: dict[str, str], names: set[str]) -> dict[str, str]:
    """Replaces named entries outright, for values that must never be recorded."""
    return {key: (MASK if key in names else value) for key, value in values.items()}
