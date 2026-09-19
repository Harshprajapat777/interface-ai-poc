from cua.policy.redact import MASK, Redactor, mask_all


def test_a_declared_secret_never_survives_into_text() -> None:
    redactor = Redactor.for_values(
        {"operator_id": "opr001", "password": "test-password"},
        sensitive={"password"},
    )
    assert redactor.text("signed on with test-password") == f"signed on with {MASK}"


def test_a_non_sensitive_input_is_left_readable() -> None:
    redactor = Redactor.for_values({"operator_id": "opr001"}, sensitive={"password"})
    assert redactor.text("operator opr001") == "operator opr001"


def test_obvious_pii_shapes_are_caught_without_being_declared() -> None:
    """A forgotten flag should not be the difference between safe and not."""
    redactor = Redactor()
    assert MASK in redactor.text("SSN 123-45-6789")
    assert MASK in redactor.text("card 4111 1111 1111 1111")
    assert MASK in redactor.text("email marguerite@example.com")


def test_longer_secrets_are_masked_before_shorter_ones() -> None:
    redactor = Redactor(secrets=frozenset({"pass", "password123"}))
    assert redactor.text("password123") == MASK


def test_dictionary_keys_stay_readable_so_logs_remain_useful() -> None:
    redactor = Redactor(secrets=frozenset({"hunter2"}))
    assert redactor.mapping({"password": "hunter2"}) == {"password": MASK}


def test_mask_all_removes_named_values_outright() -> None:
    masked = mask_all({"member_name": "DELACROIX", "balance": "$1.00"}, {"member_name"})
    assert masked == {"member_name": MASK, "balance": "$1.00"}
