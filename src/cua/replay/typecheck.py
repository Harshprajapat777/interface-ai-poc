"""Checking that a value read off the screen is the kind of value it claims to be.

A checkpoint proves the run reached the right screen; this proves it read the
right thing from it. Without it, a table whose columns moved would hand the
caller an account number labelled as a balance - a successful-looking result
that is quietly wrong, which is worse than a failure.

The same shapes are used in reverse at discovery time, to infer an output's type
from the value the model actually saw, so the check has something to hold to.
"""

import re

from cua.artifact.schema import ValueType

_MONEY = re.compile(r"\(?-?\$?\s?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?\)?")
_INTEGER = re.compile(r"-?\d+")
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}")
_BOOLEAN = re.compile(r"(?i)yes|no|true|false|y|n")

SHAPES: dict[ValueType, re.Pattern[str]] = {
    "money": _MONEY,
    "integer": _INTEGER,
    "date": _DATE,
    "boolean": _BOOLEAN,
}


def conforms(value: str, value_type: ValueType) -> bool:
    """True if the value has the shape its declared type promises.

    An empty value never conforms: a blank cell where a balance should be is a
    wrong read, not a balance of nothing.
    """
    text = value.strip()
    if not text:
        return False
    shape = SHAPES.get(value_type)
    return shape is None or shape.fullmatch(text) is not None


def infer(value: str) -> ValueType:
    """The narrowest type a value seen during discovery fits.

    Money needs a currency sign or cents to count, so a bare number such as an
    account suffix is typed as an integer rather than mistaken for an amount.
    """
    text = value.strip()
    if _INTEGER.fullmatch(text):
        return "integer"
    if _MONEY.fullmatch(text) and ("$" in text or "." in text):
        return "money"
    if _DATE.fullmatch(text):
        return "date"
    return "string"
