#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""worktool2 历史导入脚本的纯函数：日期、单号、集合发现。"""

import gzip
import json
from datetime import date

from services.purchase import importer as IMPORT


def test_order_no_is_stable_and_keeps_submit_day():
    moment = IMPORT.as_datetime("2026-11-21T09:30:00+08:00")
    first = IMPORT.order_no_for("cloud-order-1", moment)
    again = IMPORT.order_no_for("cloud-order-1", moment)
    other = IMPORT.order_no_for("cloud-order-2", moment)
    assert first == again
    assert first.startswith("P20261121-")
    assert len(first) == len("P20261121-") + 6
    assert first != other


def test_as_datetime_normalizes_to_beijing():
    utc = IMPORT.as_datetime("2026-11-20T23:30:00Z")
    assert utc.date() == date(2026, 11, 21)
    assert utc.hour == 7 and utc.minute == 30
    naive = IMPORT.as_datetime("2026-11-21 08:00:00")
    assert naive.utcoffset().total_seconds() == 8 * 3600
    assert IMPORT.as_day(1795219200) == date(2026, 11, 21)


def test_position_names_accepts_array_and_separators():
    assert IMPORT.position_names(["明档", "煎炸"]) == ["明档", "煎炸"]
    assert IMPORT.position_names("明档、煎炸/熟笼") == ["明档", "煎炸", "熟笼"]
    assert IMPORT.position_names(None) == []


def test_load_documents_accepts_array_wrapper_and_jsonl(tmp_path):
    array_file = tmp_path / "orders.json"
    array_file.write_text(json.dumps([{"_id": "a"}]), encoding="utf-8")
    assert IMPORT.load_documents(array_file) == [{"_id": "a"}]
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"data": [{"_id": "b"}]}), encoding="utf-8")
    assert IMPORT.load_documents(wrapped) == [{"_id": "b"}]
    lines = tmp_path / "orders.jsonl"
    lines.write_text('{"_id": "c"}\n{"_id": "d"}\n', encoding="utf-8")
    assert [row["_id"] for row in IMPORT.load_documents(lines)] == ["c", "d"]
    packed = tmp_path / "orders.jsonl.gz"
    packed.write_bytes(gzip.compress('{"_id": "e"}\n'.encode()))
    assert IMPORT.load_documents(packed) == [{"_id": "e"}]
    # 腾讯云开发导出的 .json 没有方括号，实际是逐行对象；不能按单值 JSON 解析。
    cloud = tmp_path / "orders-2026-11-22.json"
    cloud.write_text('{"_id": "f"}\n{"_id": "g"}\n', encoding="utf-8")
    assert [row["_id"] for row in IMPORT.load_documents(cloud)] == ["f", "g"]
    # 只有一条订单时 JSONL 只有一行；不能把订单自己的 items 当成集合。
    single = tmp_path / "orders-single.json"
    single.write_text(
        json.dumps({"_id": "h", "items": [{"productId": "p", "quantity": 1}]}),
        encoding="utf-8",
    )
    assert IMPORT.load_documents(single) == [
        {"_id": "h", "items": [{"productId": "p", "quantity": 1}]}
    ]


def test_discover_files_matches_eight_collections(tmp_path):
    for name in ("suppliers.json", "products-2026-11-21.json", "orders.jsonl"):
        (tmp_path / name).write_text("[]", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
    found = IMPORT.discover_files(tmp_path)
    assert set(found) == {"suppliers", "products", "orders"}


def test_number_and_bool_parsing():
    assert IMPORT.as_number("3.5") == 3.5
    assert IMPORT.as_number("") is None
    assert IMPORT.as_bool("是") is True
    assert IMPORT.as_bool("停用") is False
    assert IMPORT.as_bool("", default=True) is True
