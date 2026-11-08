from agzs_shared.ids import new_id, uuid7


def test_uuid7_shape() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"
    assert len(new_id()) == 36
