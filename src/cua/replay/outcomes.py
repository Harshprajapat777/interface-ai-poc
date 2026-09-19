"""The result contract, and how a runtime condition is classified.

The distinction this module exists to keep straight: "no such member" is an
answer the caller needs, not a crash. Conflating the two is the mistake the
brief calls out, so the two are different statuses carrying different fields,
and a business outcome is never raised.

Four statuses, and each means something different to the caller:

    success           the flow finished and the declared outputs are here
    business_outcome  the app answered, and the answer is a legitimate "no"
    rejected          the caller's inputs were wrong; the app was never touched
    failure           something broke; here is what to look at

`rejected` is separate from `failure` on purpose. A malformed member number is
the calling agent's bug, caught before a browser opens; a missing Search button
is ours. Handing both back as "failure" would tell an on-call engineer to go
looking at the wrong system.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from cua.artifact.schema import Capability, Extraction, OutcomeRule
from cua.surface.base import Observation

Status = Literal["success", "business_outcome", "rejected", "failure"]


@dataclass(frozen=True, slots=True)
class StepRecord:
    """What happened on one step, for the evidence log."""

    step_id: str
    action: str
    # Which locator strategy resolved the target, so weak matches are visible.
    matched_by: str = ""
    note: str = ""


@dataclass(slots=True)
class ReplayResult:
    """What a replay hands back to its caller."""

    status: Status
    message: str = ""
    outputs: dict[str, str] = field(default_factory=dict)
    # Which OutcomeRule fired, when the status is business_outcome.
    outcome: str = ""
    # Failure detail. Populated only for failure, and the point is debuggability.
    failed_step: str = ""
    expected: str = ""
    observed: str = ""
    steps_run: int = 0
    recoveries: list[str] = field(default_factory=list)
    screenshot: str = ""

    @property
    def ok(self) -> bool:
        """True only when the flow completed and produced its outputs."""
        return self.status == "success"


def find_outcome(capability: Capability, observation: Observation) -> OutcomeRule | None:
    """Returns the first declared outcome whose signal text is on screen."""
    haystack = "\n".join(observation.texts)
    for rule in capability.outcomes:
        if rule.when_text in haystack:
            return rule
    return None


def extract_value(extraction: Extraction, observation: Observation) -> str | None:
    """Pulls one declared output off the current screen."""
    if extraction.kind == "row_cell":
        return _row_cell(extraction, observation)
    return _pattern(extraction, observation)


def _row_cell(extraction: Extraction, observation: Observation) -> str | None:
    """Finds the row containing a known label and returns one of its cells.

    Legacy tables come back as a single line of tab-separated cells, so this
    keys off the row's content rather than its position in the table.
    """
    if extraction.row_contains is None or extraction.cell is None:
        return None
    for line in observation.texts:
        if extraction.row_contains not in line:
            continue
        cells = line.split("\t")
        if extraction.cell < len(cells):
            return cells[extraction.cell].strip()
    return None


def _pattern(extraction: Extraction, observation: Observation) -> str | None:
    """Returns the first capture group matched anywhere in the visible text."""
    if not extraction.pattern:
        return None
    match = re.search(extraction.pattern, "\n".join(observation.texts))
    return match.group(1) if match else None
