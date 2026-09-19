"""Checking and substituting the inputs a caller supplies.

Input validation happens before a browser is opened. A member number that
cannot be right is worth rejecting in a millisecond rather than after a
twelve-second round trip through a legacy UI, and it keeps malformed values
from ever reaching the app.
"""

import re

from cua.artifact.schema import Capability, ParamSpec
from cua.shared.result import Err, Ok, Result

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _check_one(spec: ParamSpec, value: str) -> str | None:
    """Returns a complaint about one value, or None if it is acceptable."""
    if spec.type == "integer" and not value.lstrip("-").isdigit():
        return f"{spec.name} must be a whole number, got {value!r}"
    if spec.pattern and not re.fullmatch(spec.pattern, value):
        return f"{spec.name} does not match {spec.pattern}"
    return None


def validate(capability: Capability, values: dict[str, str]) -> Result[dict[str, str], str]:
    """Checks supplied inputs against the capability's declared parameters."""
    unknown = sorted(set(values) - capability.input_names())
    if unknown:
        return Err(f"Unexpected inputs: {', '.join(unknown)}")

    for spec in capability.inputs:
        if spec.name not in values:
            if spec.required:
                return Err(f"Missing required input: {spec.name}")
            continue
        complaint = _check_one(spec, values[spec.name])
        if complaint:
            return Err(complaint)

    return Ok(values)


def fill(template: str, values: dict[str, str]) -> Result[str, str]:
    """Replaces {{name}} placeholders in a recorded value with supplied inputs."""
    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing.append(key)
            return ""
        return values[key]

    filled = PLACEHOLDER.sub(substitute, template)
    if missing:
        return Err(f"No value supplied for: {', '.join(sorted(set(missing)))}")
    return Ok(filled)


def placeholders(template: str) -> set[str]:
    """Every parameter name referenced by a recorded value."""
    return set(PLACEHOLDER.findall(template))


def undeclared_placeholders(capability: Capability) -> set[str]:
    """Placeholders used by steps that no declared input can fill.

    A capability that references {{member_id}} without declaring it is broken
    before it ever runs, so this is worth catching at review time.
    """
    used: set[str] = set()
    for step in capability.steps:
        if step.value:
            used |= placeholders(step.value)
    return used - capability.input_names()
