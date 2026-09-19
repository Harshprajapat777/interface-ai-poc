"""Command line options shared by the discover and replay entry points."""

import os


def pairs(values: list[str]) -> dict[str, str]:
    """Parses repeated key=value options."""
    parsed = {}
    for item in values:
        key, _, value = item.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed


def secrets(names: list[str]) -> dict[str, str]:
    """Reads named credentials from the environment, never from the command line.

    A password on a command line ends up in shell history and in the process
    table, so the value is looked up here and the name is all that is typed.
    """
    found = {}
    for name in names:
        variable = f"CUA_SECRET_{name.upper()}"
        value = os.environ.get(variable)
        if not value:
            raise SystemExit(f"Set {variable} for the {name!r} credential.")
        found[name] = value
    return found
