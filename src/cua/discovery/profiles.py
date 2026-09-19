"""App profiles: what is known about an application, independent of any one flow.

Outcome rules belong to the application, not to a recorded flow. "No member
found" means the same thing whether you were reading a balance or opening a
sub-account, and it is learned once by whoever integrates the app rather than
rediscovered by every recording.

Keeping them in a per-product profile is also the multi-tenant seam: hundreds
of institutions running the same vendor build share one profile, and a tenant
that words an error differently overrides a single rule instead of re-recording
every capability it owns.
"""

import json
from pathlib import Path

from cua.artifact.schema import OutcomeRule

DEFAULT_ROOT = Path("app_profiles")


def load_outcomes(product: str, root: Path = DEFAULT_ROOT) -> list[OutcomeRule]:
    """Reads the outcome rules known for one product, or none if it has no profile."""
    path = root / f"{product}.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [OutcomeRule.model_validate(rule) for rule in raw.get("outcomes", [])]
