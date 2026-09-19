import pytest

from cua.shared.result import Err, Ok, unwrap


def test_ok_carries_its_value() -> None:
    assert Ok(42).value == 42


def test_err_carries_its_error() -> None:
    assert Err("not_found").error == "not_found"


def test_unwrap_returns_the_value() -> None:
    assert unwrap(Ok("balance")) == "balance"


def test_unwrap_raises_on_a_failed_result() -> None:
    with pytest.raises(ValueError, match="not_found"):
        unwrap(Err("not_found"))
