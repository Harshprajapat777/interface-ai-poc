"""Bringing a human into a run, and getting out of their way while they work.

Three things have to be true for a handoff to be real rather than decorative.

The person gets the *same* session. Not a fresh browser pointed at the same
app - the one that is already signed on, already three screens deep, already
holding whatever state got it there. Opening a second session would lose all
of that and, on a real core banking system, would often be refused outright
because the record is already locked by the first one.

Exactly one party is in control at a time, and it is written down. Automation
that keeps clicking while an operator is mid-form is worse than automation that
stops, so control is an explicit owner that has to be handed over and handed
back, and acting out of turn is an error rather than a race.

What the human did is recorded. They are a step in the flow like any other, and
a run that cannot say what happened during the two minutes a person was driving
is not auditable.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Literal, Protocol
from uuid import uuid4

from cua.surface.base import Observation, Surface


class Owner(Enum):
    """Who may act on the session right now."""

    AUTOMATION = "automation"
    OPERATOR = "operator"


# Why the run stopped. Each one reaches a person for a different reason.
StuckKind = Literal["hard_failure", "risky_step", "unrecoverable_outcome"]


@dataclass(frozen=True, slots=True)
class Intervention:
    """The request a human is asked to act on.

    Everything here exists so the operator can pick up a run they have never
    seen: which capability, which step, why it stopped, and what is on screen.
    """

    id: str
    capability: str
    goal: str
    step_id: str
    kind: StuckKind
    reason: str
    url: str
    screen: list[str]
    screenshot: str = ""
    raised_at: str = ""

    @staticmethod
    def raise_for(
        capability: str,
        goal: str,
        step_id: str,
        kind: StuckKind,
        reason: str,
        observation: Observation,
        screenshot: str = "",
    ) -> "Intervention":
        """Builds a request from the state the run stopped in."""
        return Intervention(
            id=uuid4().hex[:12],
            capability=capability,
            goal=goal,
            step_id=step_id,
            kind=kind,
            reason=reason,
            url=observation.url,
            screen=observation.texts,
            screenshot=screenshot,
            raised_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    """What the human decided, and what changed while they held control."""

    outcome: Literal["resumed", "abandoned"]
    note: str = ""
    # Lines that appeared on screen while the operator was driving. This is the
    # honest record: we cannot see their mouse, but we can see what they changed.
    changes: list[str] = field(default_factory=list)
    url_before: str = ""
    url_after: str = ""
    trace: str = ""


class Operator(Protocol):
    """Something that can take control of a session and hand it back.

    A console prompt and a real co-browsing product differ only in how they
    implement this. Everything above it is unchanged.
    """

    def take_over(self, intervention: Intervention, surface: Surface) -> Resolution:
        """Works the session by hand, then returns control."""
        ...


class ControlTransfer:
    """Tracks who holds the session, and refuses action by whoever does not."""

    def __init__(self) -> None:
        self.owner: Owner = Owner.AUTOMATION

    def current(self) -> Owner:
        """Who holds the session right now. The brief asks that this be answerable."""
        return self.owner

    def check(self, actor: Owner) -> None:
        """Raises if the actor is not the one currently in control."""
        if self.owner is not actor:
            raise PermissionError(
                f"{actor.value} tried to act while {self.owner.value} holds the session"
            )

    def to_operator(self) -> "_Held":
        """Hands control over for the duration of a block, then takes it back."""
        return _Held(self)


class _Held:
    """Context manager that moves control to the operator and back again."""

    def __init__(self, transfer: ControlTransfer) -> None:
        self._transfer = transfer

    def __enter__(self) -> ControlTransfer:
        self._transfer.owner = Owner.OPERATOR
        return self._transfer

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Control comes back even if the operator's session blew up, because a
        # session nobody owns is the one state the run cannot recover from.
        self._transfer.owner = Owner.AUTOMATION


class Escalation:
    """Routes a stuck run to a person and reports what they did."""

    def __init__(
        self,
        operator: Operator,
        transfer: ControlTransfer | None = None,
        evidence_dir: Path | None = None,
    ) -> None:
        self.operator = operator
        self.transfer = transfer or ControlTransfer()
        self.evidence_dir = evidence_dir
        self.history: list[tuple[Intervention, Resolution]] = []

    def request(self, intervention: Intervention, surface: Surface) -> Resolution:
        """Hands the live session to a person and takes it back afterwards."""
        before = surface.observe()
        trace = self._start_trace(surface, intervention)

        with self.transfer.to_operator():
            resolution = self.operator.take_over(intervention, surface)

        after = surface.observe()
        recorded = Resolution(
            outcome=resolution.outcome,
            note=resolution.note,
            changes=_new_lines(before, after),
            url_before=before.url,
            url_after=after.url,
            trace=self._stop_trace(surface, trace),
        )
        self.history.append((intervention, recorded))
        return recorded

    def _start_trace(self, surface: Surface, intervention: Intervention) -> str:
        """Begins recording the session, if the surface can."""
        if self.evidence_dir is None:
            return ""
        path = self.evidence_dir / f"handoff-{intervention.id}.zip"
        surface.start_recording(path)
        return str(path)

    def _stop_trace(self, surface: Surface, path: str) -> str:
        """Stops recording and returns where the trace landed."""
        if not path:
            return ""
        surface.stop_recording()
        return path


def _new_lines(before: Observation, after: Observation) -> list[str]:
    """Text that is on screen now and was not before the operator took over."""
    seen = set(before.texts)
    return [line for line in after.texts if line not in seen]
