from cua.artifact.schema import AppRef, Capability, RecordMeta, Step
from cua.policy.allowlist import Allowlist, Policy, looks_irreversible

CAPABILITY = Capability(
    name="demo",
    description="d",
    app=AppRef(product="p", base_url="http://127.0.0.1:4173"),
    steps=[],
    recorded=RecordMeta(recorded_at="2026-09-20T00:00:00Z"),
)


def test_the_default_allowlist_is_the_artifacts_own_app() -> None:
    policy = Policy.for_capability(CAPABILITY)
    assert policy.check_url("http://127.0.0.1:4173/search") is None
    assert policy.check_url("http://example.com/search") is not None


def test_an_off_allowlist_host_is_refused_with_a_reason() -> None:
    denial = Policy.for_capability(CAPABILITY).check_url("http://evil.test/")
    assert denial is not None
    assert denial.rule == "host_not_allowed"
    assert "evil.test" in denial.detail


def test_paths_can_be_narrowed_below_the_host() -> None:
    allowlist = Allowlist(hosts=frozenset({"app"}), path_prefixes=("/member",))
    assert allowlist.check_url("http://app/member/12345") is None
    denial = allowlist.check_url("http://app/admin")
    assert denial is not None
    assert denial.rule == "path_not_allowed"


def test_action_types_can_be_restricted() -> None:
    read_only = Allowlist(hosts=frozenset({"app"}), actions=frozenset({"navigate", "extract"}))
    assert read_only.check_action("extract") is None
    denial = read_only.check_action("click")
    assert denial is not None
    assert denial.rule == "action_not_allowed"


def test_risky_steps_are_refused_by_default() -> None:
    """Nobody choosing should mean the conservative option, not the permissive one."""
    step = Step(id="s9", action="click", description="Post the transfer.", risk="risky")
    denial = Policy.for_capability(CAPABILITY).check_step(step)
    assert denial is not None
    assert denial.rule == "risky_action_blocked"


def test_risky_steps_can_be_routed_to_a_human_instead() -> None:
    step = Step(id="s9", action="click", description="Post the transfer.", risk="risky")
    policy = Policy.for_capability(CAPABILITY, risky="escalate")
    assert policy.check_step(step) is None
    assert policy.needs_human(step)


def test_safe_steps_are_never_treated_as_risky() -> None:
    step = Step(id="s1", action="click", description="Open the record.")
    policy = Policy.for_capability(CAPABILITY, risky="escalate")
    assert policy.check_step(step) is None
    assert not policy.needs_human(step)


def test_money_moving_and_destructive_controls_are_flagged() -> None:
    for wording in (
        "Transfer Funds",
        "Confirm Transfer",
        "Make a Payment",
        "Close Account",
        "Delete Member",
        "Open New Sub-Account",
        "Post Transaction",
        "Approve Loan",
    ):
        assert looks_irreversible(wording), wording


def test_the_servicing_consoles_own_buttons_are_not_flagged() -> None:
    """A tripwire that fires on "Search" would be switched off within a day."""
    for wording in ("Sign On", "Search", "Open", "Confirm Consent", "Back to Search", "Payday"):
        assert not looks_irreversible(wording), wording
