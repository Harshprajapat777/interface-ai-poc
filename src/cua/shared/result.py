"""A returned success-or-failure value.

Nothing in this project raises to signal an expected condition. A "no such member"
result is an answer the caller needs, not a crash - see ARCHITECTURE.md section 4.
Only genuine faults (a bug, a broken dependency) are allowed to raise.
"""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """A successful result carrying its value."""

    value: T


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    """A failed result carrying its error."""

    error: E


Result: TypeAlias = Ok[T] | Err[E]


def unwrap(result: Result[T, E]) -> T:
    """Returns the value, or raises - use only where a failure really is a bug."""
    if isinstance(result, Err):
        raise ValueError(f"Tried to unwrap a failed Result: {result.error!r}")
    return result.value
