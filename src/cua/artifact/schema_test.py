from pathlib import Path

import pytest
from pydantic import ValidationError

from cua.artifact import store
from cua.artifact.params import fill, undeclared_placeholders, validate
from cua.artifact.schema import Capability
from cua.shared.result import Err, Ok

FIXTURE = Path("tests/fixtures/get_savings_balance_handwritten.json")


@pytest.fixture
def capability() -> Capability:
    return store.load_file(FIXTURE)


def test_the_handwritten_artifact_validates(capability: Capability) -> None:
    assert capability.name == "get_savings_balance"
    assert len(capability.steps) == 8


def test_unknown_fields_are_rejected() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")
    raw = raw.replace('"version": 1,', '"version": 1, "oops": 1,', 1)
    with pytest.raises(ValidationError):
        Capability.model_validate_json(raw)


def test_round_trips_through_json(capability: Capability, tmp_path: Path) -> None:
    store.save(capability, root=tmp_path)
    assert store.load("get_savings_balance", root=tmp_path) == capability


def test_versions_and_next_version(capability: Capability, tmp_path: Path) -> None:
    store.save(capability, root=tmp_path)
    store.save(capability.model_copy(update={"version": 2}), root=tmp_path)
    assert store.versions("get_savings_balance", root=tmp_path) == [1, 2]
    assert store.next_version("get_savings_balance", root=tmp_path) == 3


def test_load_defaults_to_the_newest_version(capability: Capability, tmp_path: Path) -> None:
    store.save(capability, root=tmp_path)
    store.save(capability.model_copy(update={"version": 7}), root=tmp_path)
    assert store.load("get_savings_balance", root=tmp_path).version == 7


def test_password_is_marked_sensitive(capability: Capability) -> None:
    assert capability.sensitive_names() == {"password"}


def test_tool_schema_describes_the_capability_to_an_agent(capability: Capability) -> None:
    tool = capability.tool_schema()
    schema = tool["input_schema"]
    assert isinstance(schema, dict)
    assert set(schema["properties"]) == {"operator_id", "password", "member_id"}
    assert schema["required"] == ["operator_id", "password", "member_id"]
    assert schema["properties"]["member_id"]["pattern"] == "^[0-9]{5}$"


def test_every_placeholder_has_a_declared_input(capability: Capability) -> None:
    assert undeclared_placeholders(capability) == set()


def test_validate_accepts_good_inputs(capability: Capability) -> None:
    values = {"operator_id": "opr001", "password": "x", "member_id": "12345"}
    assert validate(capability, values) == Ok(values)


def test_validate_rejects_a_malformed_member_number(capability: Capability) -> None:
    values = {"operator_id": "opr001", "password": "x", "member_id": "abc"}
    result = validate(capability, values)
    assert isinstance(result, Err)
    assert "whole number" in result.error


def test_validate_rejects_a_missing_input(capability: Capability) -> None:
    result = validate(capability, {"operator_id": "opr001", "password": "x"})
    assert result == Err("Missing required input: member_id")


def test_validate_rejects_unknown_inputs(capability: Capability) -> None:
    values = {"operator_id": "opr001", "password": "x", "member_id": "12345", "extra": "1"}
    assert validate(capability, values) == Err("Unexpected inputs: extra")


def test_fill_substitutes_placeholders() -> None:
    assert fill("{{member_id}}", {"member_id": "12345"}) == Ok("12345")


def test_fill_reports_a_missing_value() -> None:
    assert fill("{{member_id}}", {}) == Err("No value supplied for: member_id")


def test_outcomes_cover_all_three_classes(capability: Capability) -> None:
    kinds = {outcome.kind for outcome in capability.outcomes}
    assert kinds == {"business", "recoverable", "hard"}


def test_catalog_lists_the_newest_of_each(capability: Capability, tmp_path: Path) -> None:
    store.save(capability, root=tmp_path)
    store.save(capability.model_copy(update={"version": 2}), root=tmp_path)
    listed = store.catalog(root=tmp_path)
    assert [(c.name, c.version) for c in listed] == [("get_savings_balance", 2)]
