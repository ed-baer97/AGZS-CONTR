import pytest

from agzs_shared.integrity import GENESIS_HASH, canonical_json, chain_hash


def test_canonical_json_orders_keys() -> None:
    assert canonical_json({"b": 1, "a": "тенге"}) == '{"a":"тенге","b":1}'


def test_chain_is_stable_and_rejects_float() -> None:
    payload = {"amount_kzt": 100, "name": "колонка"}
    first = chain_hash(GENESIS_HASH, payload)
    assert first == chain_hash(GENESIS_HASH, payload)
    assert len(first) == 64
    with pytest.raises(TypeError):
        chain_hash(GENESIS_HASH, {"price": 1.5})
