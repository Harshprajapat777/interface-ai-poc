"""Verifying that a step actually did what it was supposed to do.

Checkpoints are why replay is not just a macro. Clicking Search and assuming a
results table appeared is how automation silently reads the wrong screen; the
checkpoint is the assertion that turns that into a reported failure.

Waiting is done by polling the surface rather than sleeping a fixed amount.
That is what absorbs transient slowness - the brief's "slow load" case - without
padding every step with a pause that costs time on every run.
"""

import time
from collections.abc import Callable

from cua.artifact.schema import Checkpoint
from cua.surface.base import Observation, Target, match_control

POLL_INTERVAL_SECONDS = 0.25


def is_satisfied(checkpoint: Checkpoint, observation: Observation) -> bool:
    """True if the current screen meets the checkpoint."""
    if checkpoint.kind == "text_present":
        return _has_text(checkpoint.value, observation)
    if checkpoint.kind == "text_absent":
        return not _has_text(checkpoint.value, observation)
    if checkpoint.kind == "url_contains":
        return checkpoint.value in observation.url
    return match_control(observation, Target(role=checkpoint.value)) is not None


def wait_until(
    checkpoint: Checkpoint,
    observe: Callable[[], Observation],
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, Observation]:
    """Polls until the checkpoint holds or its timeout expires.

    Returns whether it held, and the last observation taken - which is what the
    failure report needs in order to say what was actually on screen.
    """
    deadline = now() + checkpoint.timeout_ms / 1000
    observation = observe()
    while True:
        if is_satisfied(checkpoint, observation):
            return True, observation
        if now() >= deadline:
            return False, observation
        sleep(POLL_INTERVAL_SECONDS)
        observation = observe()


def describe(checkpoint: Checkpoint) -> str:
    """A short phrase for the failure report."""
    return f"{checkpoint.kind}={checkpoint.value!r}"


def _has_text(needle: str, observation: Observation) -> bool:
    """True if the text appears anywhere on screen."""
    return any(needle in line for line in observation.texts)
