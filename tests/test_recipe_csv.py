"""CSV structured-column codec: JSON cells for ingredients / steps / tips."""

import pytest

from services.recipes.csv_codec import (
    CsvStructuredDecodeError,
    decode_ingredients_cell,
    decode_string_list_cell,
    encode_json_cell,
)


def test_encode_ingredients_as_json_array():
    assert encode_json_cell([{"name": "面粉", "amount": "200", "unit": "g"}]) == (
        '[{"name": "面粉", "amount": "200", "unit": "g"}]'
    )


def test_decode_ingredients_cell_reads_json_array():
    assert decode_ingredients_cell(
        '[{"name": "面粉", "amount": "200", "unit": "g"}]'
    ) == [{"name": "面粉", "amount": "200", "unit": "g"}]


def test_decode_ingredients_cell_empty_is_empty_list():
    assert decode_ingredients_cell("") == []
    assert decode_ingredients_cell(None) == []
    assert decode_ingredients_cell("   ") == []


def test_decode_ingredients_cell_invalid_json_raises():
    with pytest.raises(CsvStructuredDecodeError) as exc:
        decode_ingredients_cell("not-json")
    assert "用料" in str(exc.value)


def test_decode_ingredients_cell_non_array_raises():
    with pytest.raises(CsvStructuredDecodeError):
        decode_ingredients_cell('{"name": "面粉"}')


def test_encode_steps_as_json_array():
    assert encode_json_cell(["混合面粉与水", "静置 10 分钟"]) == (
        '["混合面粉与水", "静置 10 分钟"]'
    )


def test_decode_string_list_cell_reads_json_array():
    assert decode_string_list_cell(
        '["夏天水温要更低"]', column_label="小贴士",
    ) == ["夏天水温要更低"]


def test_decode_string_list_cell_empty_is_empty_list():
    assert decode_string_list_cell("", column_label="步骤") == []
    assert decode_string_list_cell(None, column_label="步骤") == []


def test_decode_string_list_cell_invalid_json_raises():
    with pytest.raises(CsvStructuredDecodeError) as exc:
        decode_string_list_cell("[", column_label="步骤")
    assert "步骤" in str(exc.value)
