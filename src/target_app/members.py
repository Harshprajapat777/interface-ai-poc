"""Seeded member data for the fake servicing console.

Every member exists to make one runtime condition reachable on demand, so replay
error handling can be demonstrated rather than described. None of this is real
data - the names are invented and the account numbers are not valid anywhere.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Account:
    """One deposit account shown on the member detail screen."""

    kind: str
    number: str
    balance: str


@dataclass(frozen=True, slots=True)
class Member:
    """A member record, plus the runtime condition looking them up triggers."""

    number: str
    name: str
    accounts: list[Account] = field(default_factory=list)
    # Operator is not entitled to this record - the app answers with a denial page.
    restricted: bool = False
    # The app interrupts with a consent interstitial before showing the detail screen.
    needs_consent: bool = False


MEMBERS: dict[str, Member] = {
    # Happy path.
    "12345": Member(
        number="12345",
        name="DELACROIX, MARGUERITE",
        accounts=[
            Account("Share Savings", "0001", "$4,182.55"),
            Account("Share Draft", "0002", "$914.20"),
            Account("Certificate 12mo", "0031", "$10,000.00"),
        ],
    ),
    # Recoverable: an unexpected interstitial the automation has to get past.
    "77777": Member(
        number="77777",
        name="OKONKWO, ADAEZE",
        accounts=[
            Account("Share Savings", "0001", "$27.03"),
            Account("Share Draft", "0002", "$1,506.88"),
        ],
        needs_consent=True,
    ),
    # Business outcome: a permission denial, not a crash.
    "55555": Member(
        number="55555",
        name="[RESTRICTED]",
        accounts=[],
        restricted=True,
    ),
}


def find_member(number: str) -> Member | None:
    """Looks up a member by number, or None if there is no such record."""
    return MEMBERS.get(number)


def savings_balance(member: Member) -> str | None:
    """Returns the member's share savings balance, or None if they have no savings account."""
    for account in member.accounts:
        if account.kind == "Share Savings":
            return account.balance
    return None
