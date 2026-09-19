"""Matching is a pure function over a snapshot, so it tests without a browser."""

from cua.surface.base import Control, Observation, Target, match_control


def control(ref: str, role: str, name: str = "", label: str = "", index: int = 0) -> Control:
    return Control(ref=ref, role=role, name=name, label=label, frame="", index=index)


def observation(*controls: Control) -> Observation:
    return Observation(url="http://app", title="t", controls=list(controls), texts=[])


def test_prefers_an_exact_accessible_name() -> None:
    seen = observation(
        control("c1", "button", name="Cancel"),
        control("c2", "button", name="Search"),
    )
    found = match_control(seen, Target(role="button", name="Search"))
    assert found is not None
    assert (found[0].ref, found[1]) == ("c2", "name")


def test_falls_back_to_the_nearest_label_when_there_is_no_name() -> None:
    seen = observation(
        control("c1", "textbox", label="Operator ID"),
        control("c2", "textbox", label="Password"),
    )
    found = match_control(seen, Target(role="textbox", label="Password"))
    assert found is not None
    assert (found[0].ref, found[1]) == ("c2", "label")


def test_falls_back_to_a_partial_name_when_wording_changed() -> None:
    seen = observation(control("c1", "button", name="Search Members"))
    found = match_control(seen, Target(role="button", name="Search"))
    assert found is not None
    assert (found[0].ref, found[1]) == ("c1", "partial_name")


def test_falls_back_to_position_as_a_last_resort() -> None:
    seen = observation(control("c1", "textbox", index=0), control("c2", "textbox", index=1))
    found = match_control(seen, Target(role="textbox", index=1))
    assert found is not None
    assert (found[0].ref, found[1]) == ("c2", "index")


def test_returns_nothing_when_the_control_is_gone() -> None:
    seen = observation(control("c1", "button", name="Cancel"))
    assert match_control(seen, Target(role="button", name="Search")) is None


def test_ignores_controls_in_a_different_frame() -> None:
    other = Control(ref="c1", role="button", name="Search", label="", frame="navframe", index=0)
    seen = Observation(url="u", title="t", controls=[other], texts=[])
    assert match_control(seen, Target(role="button", name="Search", frame="contentframe")) is None
