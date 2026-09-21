from cua.artifact.rebase import rebase
from cua.artifact.schema import AppRef, Capability, RecordMeta, Step

RECORDED = Capability(
    name="get_savings_balance",
    description="d",
    app=AppRef(product="console", tenant="demo-cu", base_url="http://first.example"),
    steps=[
        Step(id="s1", action="navigate", description="Open.", value="http://first.example"),
        Step(id="s2", action="fill", description="Type.", value="{{member_id}}"),
    ],
    recorded=RecordMeta(recorded_at="2026-09-21T00:00:00Z"),
)


def test_every_recorded_address_moves_to_the_new_instance() -> None:
    moved = rebase(RECORDED, "http://second.example")
    assert moved.app.base_url == "http://second.example"
    assert moved.steps[0].value == "http://second.example"


def test_nothing_but_the_address_is_touched() -> None:
    """A renamed button between tenants is a real difference, not a find and replace."""
    moved = rebase(RECORDED, "http://second.example")
    assert moved.steps[1].value == "{{member_id}}"
    assert [s.id for s in moved.steps] == ["s1", "s2"]


def test_the_new_instance_can_be_attributed_to_its_institution() -> None:
    moved = rebase(RECORDED, "http://second.example", tenant="riverbend-cu")
    assert moved.app.tenant == "riverbend-cu"


def test_the_original_is_left_alone() -> None:
    rebase(RECORDED, "http://second.example")
    assert RECORDED.app.base_url == "http://first.example"
