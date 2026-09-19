from cua.discovery.recorder import _target
from cua.surface.base import Control


def test_a_named_control_does_not_record_the_text_beside_it() -> None:
    """That text is often the member record the control sits next to."""
    row_button = Control(
        ref="c1",
        role="button",
        name="Open",
        label="DELACROIX, MARGUERITE",
        frame="contentframe",
        index=0,
    )
    spec = _target(row_button)
    assert spec.recorded_strategy == "name"
    assert spec.label == ""


def test_a_nameless_control_keeps_the_label_it_needs() -> None:
    login_box = Control(ref="c2", role="textbox", name="", label="Operator ID", frame="", index=0)
    spec = _target(login_box)
    assert spec.recorded_strategy == "label"
    assert spec.label == "Operator ID"


def test_a_control_with_neither_falls_back_to_position() -> None:
    bare = Control(ref="c3", role="textbox", name="", label="", frame="", index=2)
    spec = _target(bare)
    assert spec.recorded_strategy == "index"
    assert spec.index == 2
