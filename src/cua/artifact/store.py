"""Reading and writing capability artifacts.

Artifacts are plain JSON files on disk, one per version:

    artifacts/get_savings_balance/v1.json

A directory of readable JSON is deliberate. These are meant to be reviewed in
a pull request and diffed between versions, which a database row is not. A real
deployment would put the same documents behind a registry service; nothing above
this module would notice, because it only ever asks for a name and a version.
"""

import json
from pathlib import Path

from cua.artifact.schema import Capability

DEFAULT_ROOT = Path("artifacts")


def _directory(name: str, root: Path) -> Path:
    """Where every version of one capability lives."""
    return root / name


def _path(name: str, version: int, root: Path) -> Path:
    """The file holding one specific version."""
    return _directory(name, root) / f"v{version}.json"


def versions(name: str, root: Path = DEFAULT_ROOT) -> list[int]:
    """Every saved version of a capability, oldest first."""
    directory = _directory(name, root)
    if not directory.is_dir():
        return []
    found = [int(p.stem[1:]) for p in directory.glob("v*.json") if p.stem[1:].isdigit()]
    return sorted(found)


def next_version(name: str, root: Path = DEFAULT_ROOT) -> int:
    """The version number a new recording of this capability should take."""
    saved = versions(name, root)
    return saved[-1] + 1 if saved else 1


def save(capability: Capability, root: Path = DEFAULT_ROOT) -> Path:
    """Writes a capability to disk and returns where it landed."""
    path = _path(capability.name, capability.version, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(capability.model_dump_json(indent=2), encoding="utf-8")
    return path


def load(name: str, version: int | None = None, root: Path = DEFAULT_ROOT) -> Capability:
    """Loads one capability, defaulting to its newest version."""
    wanted = version if version is not None else _latest(name, root)
    path = _path(name, wanted, root)
    if not path.is_file():
        raise FileNotFoundError(f"No artifact at {path}")
    return Capability.model_validate_json(path.read_text(encoding="utf-8"))


def load_file(path: Path) -> Capability:
    """Loads a capability from an explicit path."""
    return Capability.model_validate_json(path.read_text(encoding="utf-8"))


def catalog(root: Path = DEFAULT_ROOT) -> list[Capability]:
    """The newest version of every saved capability - what an agent would browse."""
    if not root.is_dir():
        return []
    names = sorted(p.name for p in root.iterdir() if p.is_dir())
    return [load(name, root=root) for name in names if versions(name, root)]


def _latest(name: str, root: Path) -> int:
    """The newest version number, or an error if nothing is saved."""
    saved = versions(name, root)
    if not saved:
        raise FileNotFoundError(f"No artifact named {name!r} under {root}")
    return saved[-1]


def write_tool_catalog(path: Path, root: Path = DEFAULT_ROOT) -> Path:
    """Dumps every capability as JSON-Schema tool definitions for a calling agent."""
    tools = [capability.tool_schema() for capability in catalog(root)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tools, indent=2), encoding="utf-8")
    return path
