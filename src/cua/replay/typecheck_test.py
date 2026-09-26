from cua.replay.typecheck import conforms, infer


def test_a_balance_is_money() -> None:
    assert conforms("$4,182.55", "money")
    assert conforms("(1,000.00)", "money")
    assert conforms("27.03", "money")


def test_an_account_label_read_by_mistake_is_not_money() -> None:
    """The case this exists for: the wrong cell, returned as if it were right."""
    assert not conforms("Share Savings", "money")
    assert not conforms("0001-S", "money")


def test_a_blank_cell_never_conforms() -> None:
    assert not conforms("  ", "string")
    assert not conforms("", "money")


def test_a_string_accepts_any_text() -> None:
    assert conforms("DELACROIX, MARGUERITE", "string")


def test_dates_and_integers() -> None:
    assert conforms("09/25/2026", "date")
    assert conforms("2026-09-25", "date")
    assert conforms("12345", "integer")
    assert not conforms("12,a", "integer")


def test_inference_prefers_the_narrowest_type() -> None:
    assert infer("$4,182.55") == "money"
    assert infer("0001") == "integer"
    assert infer("09/25/2026") == "date"
    assert infer("Share Savings") == "string"
