"""Encode and decode structured recipe fields as CSV JSON cells."""

from __future__ import annotations

import json

INGREDIENT_KEYS = ("name", "amount", "unit")


class CsvStructuredDecodeError(ValueError):
    def __init__(self, column_label: str):
        self.column_label = column_label
        super().__init__(f"{column_label}列不是合法 JSON 数组")


def encode_json_cell(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def decode_ingredients_cell(raw) -> list[dict]:
    data = _decode_json_array(raw, column_label="用料")
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        row = {}
        for key in INGREDIENT_KEYS:
            cell = item.get(key)
            row[key] = "" if cell is None else str(cell).strip()
        if not any(row[key] for key in INGREDIENT_KEYS):
            continue
        out.append(row)
    return out


def decode_string_list_cell(raw, *, column_label: str) -> list[str]:
    data = _decode_json_array(raw, column_label=column_label)
    out: list[str] = []
    for item in data:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            out.append(text)
    return out


def _decode_json_array(raw, *, column_label: str) -> list:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CsvStructuredDecodeError(column_label) from exc
    if not isinstance(data, list):
        raise CsvStructuredDecodeError(column_label)
    return data
