"""What the automation is permitted to do, and where.

The allowlist defaults to the one application the artifact was recorded against,
not to "anything not forbidden". A capability that was recorded on a servicing
console has no business navigating anywhere else, and the artifact already says
which app that is - so the safe default costs nothing to configure and is the
one that holds when someone forgets to.

Enforcement happens on the URL actually reached, not only on the URL asked for.
A redirect to an off-allowlist host is exactly the case a pre-flight check on
the requested address would miss.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from cua.artifact.schema import ActionKind, Capability, Step

ALL_ACTIONS: frozenset[str] = frozenset({"navigate", "click", "fill", "extract"})

# Words on a control that mean pressing it moves money, destroys data or commits
# the institution to something. This is a tripwire for discovery, where nobody
# has reviewed the flow yet - not a classifier to trust on its own. It is tuned
# to over-trigger: a false alarm costs one operator decision, a miss can cost a
# member their money. "Confirm" alone is deliberately absent, because legacy
# apps put it on harmless acknowledgements; "confirm transfer" is still caught.
IRREVERSIBLE = re.compile(
    r"\b(transfer|withdraw\w*|pay(ment)?s?|disburse\w*|wire|delete|remove|void|reverse|"
    r"close\s+(the\s+)?account|charge[- ]?off|post\s+(the\s+)?(transaction|payment)|"
    r"approve|authori[sz]e|submit\s+(the\s+)?(application|payment|transfer)|"
    r"open\s+(a\s+|new\s+)*(sub-?)?account|create\s+(a\s+|new\s+)*account)\b",
    re.IGNORECASE,
)


def looks_irreversible(text: str) -> bool:
    """True if a control's wording suggests it cannot be undone."""
    return IRREVERSIBLE.search(text) is not None


@dataclass(frozen=True, slots=True)
class Denial:
    """A refusal to act, and why - written to be read by an operator."""

    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Allowlist:
    """The hosts, paths and action types this run may touch."""

    hosts: frozenset[str]
    path_prefixes: tuple[str, ...] = ("/",)
    actions: frozenset[str] = ALL_ACTIONS

    def check_url(self, url: str) -> Denial | None:
        """Refuses a URL outside the permitted hosts or paths."""
        parsed = urlparse(url)
        host = parsed.netloc
        if host not in self.hosts:
            return Denial("host_not_allowed", f"{host or url!r} is not in the allowlist")
        path = parsed.path or "/"
        if not any(path.startswith(prefix) for prefix in self.path_prefixes):
            return Denial("path_not_allowed", f"{path!r} is not under an allowed path")
        return None

    def check_action(self, action: ActionKind) -> Denial | None:
        """Refuses an action type this run is not permitted to perform."""
        if action not in self.actions:
            return Denial("action_not_allowed", f"{action!r} is not permitted for this run")
        return None


# How a step marked risky is treated. Default is to refuse: an irreversible
# action taken wrongly on a member account cannot be undone by retrying, so the
# conservative option is the one that should apply when nobody chose.
RiskMode = str


@dataclass(frozen=True, slots=True)
class Policy:
    """The guardrails a single replay runs under."""

    allowlist: Allowlist
    # "block" refuses, "escalate" hands the decision to a human, "allow" proceeds.
    risky: RiskMode = "block"

    @staticmethod
    def for_capability(capability: Capability, risky: RiskMode = "block") -> "Policy":
        """Builds the default policy: this artifact's own application, and nothing else."""
        host = urlparse(capability.app.base_url).netloc
        return Policy(allowlist=Allowlist(hosts=frozenset({host})), risky=risky)

    def check_step(self, step: Step) -> Denial | None:
        """Refuses a step whose action type or risk level is not permitted."""
        denial = self.allowlist.check_action(step.action)
        if denial is not None:
            return denial
        if step.risk == "risky" and self.risky == "block":
            return Denial(
                "risky_action_blocked",
                f"step {step.id} is irreversible and this run may not perform it",
            )
        return None

    def check_url(self, url: str) -> Denial | None:
        """Refuses a URL outside the allowlist."""
        return self.allowlist.check_url(url)

    def needs_human(self, step: Step) -> bool:
        """True when a risky step should be handed to a person rather than refused."""
        return step.risk == "risky" and self.risky == "escalate"
