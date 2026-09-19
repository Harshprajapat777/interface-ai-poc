"""What a surface is, and how a control on it gets identified.

This module is the seam the whole project turns on. Everything above it -
discovery, replay, the artifact schema - talks in terms of Control and
Observation and never touches Playwright. Supporting a desktop app means
writing another Surface implementation, not changing anything else.

Nothing here imports a browser library on purpose; matching is a pure
function over a snapshot, so it can be tested without launching anything.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Control:
    """One interactive control seen in a snapshot.

    `ref` is only valid for the snapshot it came from - it is how the agent
    points at something it can currently see. What gets recorded into an
    artifact is the durable part: role, name, label, frame and index.
    """

    ref: str
    role: str
    # Accessible name. Often empty on legacy markup, which is the whole problem.
    name: str
    # Nearest visible text, e.g. the cell to the left. Our fallback when name is empty.
    label: str
    frame: str
    # Position among controls sharing this role in this frame. Last-resort fallback.
    index: int


@dataclass(frozen=True, slots=True)
class Observation:
    """Everything perceived about the surface at one moment."""

    url: str
    title: str
    controls: list[Control]
    # Visible text lines, used for checkpoints and for spotting error banners.
    texts: list[str]


@dataclass(frozen=True, slots=True)
class Target:
    """How a recorded step identifies the control it needs.

    Fields are tried in order by `match_control`, so a target recorded against
    a well-named control stays readable, while one recorded against a nameless
    legacy input still has something to fall back on.
    """

    role: str
    name: str = ""
    label: str = ""
    frame: str = ""
    index: int | None = None


def _same_frame(control: Control, target: Target) -> bool:
    """True if the control is in the frame the target asked for."""
    return not target.frame or control.frame == target.frame


def _by_name(controls: list[Control], target: Target) -> Control | None:
    """Exact match on role and accessible name - the strongest signal."""
    if not target.name:
        return None
    for control in controls:
        if control.role == target.role and control.name == target.name:
            return control
    return None


def _by_label(controls: list[Control], target: Target) -> Control | None:
    """Match on the nearest visible text, for controls with no accessible name."""
    if not target.label:
        return None
    for control in controls:
        if control.role == target.role and control.label == target.label:
            return control
    return None


def _by_partial_name(controls: list[Control], target: Target) -> Control | None:
    """Case-insensitive contains, to survive cosmetic label edits."""
    if not target.name:
        return None
    wanted = target.name.casefold()
    for control in controls:
        if control.role == target.role and wanted in control.name.casefold():
            return control
    return None


def _by_index(controls: list[Control], target: Target) -> Control | None:
    """Position among controls of the same role. Weakest, but sometimes all there is."""
    if target.index is None:
        return None
    for control in controls:
        if control.role == target.role and control.index == target.index:
            return control
    return None


# Ordered strongest to weakest. Replay records which one hit, so a run that only
# succeeded on a weak strategy is visible rather than silently fragile.
STRATEGIES = (
    ("name", _by_name),
    ("label", _by_label),
    ("partial_name", _by_partial_name),
    ("index", _by_index),
)


def match_control(observation: Observation, target: Target) -> tuple[Control, str] | None:
    """Finds the control a target refers to, and reports which strategy found it."""
    candidates = [c for c in observation.controls if _same_frame(c, target)]
    for strategy_name, strategy in STRATEGIES:
        found = strategy(candidates, target)
        if found is not None:
            return found, strategy_name
    return None


class Surface(Protocol):
    """A live surface the agent can perceive and act on.

    Implement this to support a new kind of surface. A desktop implementation
    would read the OS accessibility API instead of the DOM, and everything
    above this interface would keep working unchanged.
    """

    def open(self, url: str) -> None:
        """Navigates to a starting point."""
        ...

    def observe(self) -> Observation:
        """Takes a snapshot of what is currently on screen."""
        ...

    def click(self, control: Control) -> None:
        """Clicks a control from the latest observation."""
        ...

    def fill(self, control: Control, text: str) -> None:
        """Types text into a control from the latest observation."""
        ...

    def screenshot(self, path: Path) -> None:
        """Saves a picture of the current screen."""
        ...

    def close(self) -> None:
        """Shuts the surface down."""
        ...
