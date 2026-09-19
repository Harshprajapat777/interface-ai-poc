from cua.artifact.schema import Extraction
from cua.replay.outcomes import extract_value
from cua.surface.base import Observation

DETAIL_SCREEN = Observation(
    url="http://app/member/12345",
    title="Member Detail",
    controls=[],
    texts=[
        "Member No\t12345",
        "Name\tDELACROIX, MARGUERITE",
        "Account Type\tSuffix\tCurrent Balance",
        "Share Savings\t0001\t$4,182.55",
        "Share Draft\t0002\t$914.20",
    ],
)


def test_row_cell_reads_a_cell_from_the_row_it_names() -> None:
    extraction = Extraction(kind="row_cell", row_contains="Share Savings", cell=2)
    assert extract_value(extraction, DETAIL_SCREEN) == "$4,182.55"


def test_row_cell_is_indifferent_to_where_the_row_sits() -> None:
    """Row order changing is exactly what a positional selector would not survive."""
    shuffled = Observation(
        url=DETAIL_SCREEN.url,
        title=DETAIL_SCREEN.title,
        controls=[],
        texts=list(reversed(DETAIL_SCREEN.texts)),
    )
    extraction = Extraction(kind="row_cell", row_contains="Share Savings", cell=2)
    assert extract_value(extraction, shuffled) == "$4,182.55"


def test_row_cell_returns_nothing_when_the_row_is_absent() -> None:
    extraction = Extraction(kind="row_cell", row_contains="Money Market", cell=2)
    assert extract_value(extraction, DETAIL_SCREEN) is None


def test_row_cell_returns_nothing_when_the_cell_is_out_of_range() -> None:
    extraction = Extraction(kind="row_cell", row_contains="Name", cell=9)
    assert extract_value(extraction, DETAIL_SCREEN) is None


def test_pattern_returns_its_capture_group() -> None:
    extraction = Extraction(kind="pattern", pattern=r"Member No\t(\d+)")
    assert extract_value(extraction, DETAIL_SCREEN) == "12345"


def test_pattern_returns_nothing_when_it_does_not_match() -> None:
    extraction = Extraction(kind="pattern", pattern=r"Sort Code\t(\d+)")
    assert extract_value(extraction, DETAIL_SCREEN) is None
