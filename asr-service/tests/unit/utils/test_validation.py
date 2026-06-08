"""app/utils/validation.py 单测：会话/请求级参数的类型转换 + 范围钳制。"""
import pytest

from app.utils.validation import coerce_num_in_range, parse_bool


def test_coerce_float_in_range_ok():
    assert coerce_num_in_range(0.5, (0.0, 1.0), "x") == 0.5
    assert coerce_num_in_range(1, (0.0, 1.0), "x") == 1.0   # int 写法接受，归一化为 float


def test_coerce_int_cast():
    assert coerce_num_in_range(1500, (0, 10000), "x", cast=int) == 1500
    assert isinstance(coerce_num_in_range(3.0, (0, 10), "x", cast=int), int)


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_coerce_out_of_range_raises(value):
    with pytest.raises(ValueError):
        coerce_num_in_range(value, (0.0, 1.0), "x")


@pytest.mark.parametrize("value", [True, False, "abc", None])
def test_coerce_rejects_non_number(value):
    with pytest.raises(ValueError):
        coerce_num_in_range(value, (0.0, 1.0), "x")


def test_parse_bool_default_when_none():
    assert parse_bool(None, True, "x") is True
    assert parse_bool(None, False, "x") is False


def test_parse_bool_explicit():
    assert parse_bool(True, False, "x") is True
    assert parse_bool(False, True, "x") is False


@pytest.mark.parametrize("value", ["true", 1, 0])
def test_parse_bool_rejects_non_bool(value):
    with pytest.raises(ValueError):
        parse_bool(value, True, "x")
