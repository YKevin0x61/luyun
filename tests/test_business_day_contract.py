#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DOC-06：营业日 / 自然日口径的跨语言契约（Python ⇄ KDS）。

`services/business_day.py` 与 `kds/utils/businessDay.js` 是「今天」这套概念的两份实现：

- Python 侧是**营业日**（06:00 切：06:00 之前的单属于前一营业日），
  `business_date_of()` / `current_business_date()`；
- JS 侧是**中国自然日**（00:00 切），`chinaDateKey()` —— KDS 的看单窗口与
  `api/orders.py` 收到的 `start_date`/`end_date` 用的都是这个口径
  （`businessDay.js` 的模块 docstring 已就此澄清）。

两份实现各自都有单测，但此前**没有任何一处**用同一组输入比对两边的输出：
服务端改切日规则、端侧改锚点，两边测试都会绿，而线上「今天」已经错开（DOC-06）。

**夹具共用方式**：夹具只有一份 —— `FIXTURES` 里的固定 UTC 时间戳。Python 侧直接算，
同一份 JSON 从 stdin 交给 Node 探针（`_run_node_probe`）：探针在临时目录里把
`kds/utils/businessDay.js` 的源码**原样**拷成 `businessDay.mjs` 再 `import`
（`kds/package.json` 没有 `"type": "module"`，直接 import `.js` 在部分 Node 版本上
会拒绝 ESM 语法），然后对同一组时间戳输出 `chinaDateKey()` 与 `chinaDayRange()`。
两份实现读的是完全相同的输入，所以断言的是真正的跨语言契约，而不是各自单测的复述。

被钉住的契约（改动任何一侧都会在这里红）：

1. JS `chinaDateKey(ms)` == Python 的东八区日历日（`to_china_tz(...).date()`）；
2. JS `chinaDayRange(ms)` == Python 的东八区自然日 `[00:00:00.000, 23:59:59.999]`；
3. 北京时 06:00 起，JS 的「今天」== Python 的营业日；00:00–06:00 这一段必然差一天
   —— 这不是 bug，是两套口径，差异带在这里显式钉住而不是装作不存在。

CI 的 Python job 不装 Node：没有 `node` 就 skip，不会把后端 pytest 拖红。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from db_core.utils import CHINA_TZ
from services.business_day import BUSINESS_DAY_CUT_HOUR, business_date_of, to_china_tz

# 固定 UTC 时间戳：覆盖营业日切点两侧、跨日、跨月、跨年、闰日
FIXTURES: list[tuple[str, str]] = [
    ("before-cut", "2026-09-23T21:59:59.999Z"),  # 北京 09-24 05:59:59.999
    ("at-cut", "2026-09-23T22:00:00.000Z"),  # 北京 09-24 06:00:00.000
    ("after-cut", "2026-09-23T22:00:00.001Z"),  # 北京 09-24 06:00:00.001
    ("china-midnight", "2026-09-23T16:00:00.000Z"),  # 北京 09-24 00:00:00.000
    ("night-shift", "2026-09-23T19:30:00.000Z"),  # 北京 09-24 03:30
    ("daytime", "2026-09-24T04:00:00.000Z"),  # 北京 09-24 12:00
    ("month-boundary", "2026-09-30T16:00:00.000Z"),  # 北京 10-01 00:00
    ("year-boundary", "2025-12-31T16:00:00.000Z"),  # 北京 2026-01-01 00:00
    ("leap-day", "2028-02-28T16:00:00.000Z"),  # 北京 2028-02-29 00:00
]

# Node 探针：读 stdin 的 JSON，输出同一组时间戳上 JS 侧的「今天」与「日窗口」
_NODE_PROBE_SOURCE = """
import { readFileSync } from 'node:fs'
import { chinaDateKey, chinaDayRange } from './businessDay.mjs'

const instants = JSON.parse(readFileSync(0, 'utf8')).instants

process.stdout.write(
  JSON.stringify({
    dateKeys: instants.map((ms) => chinaDateKey(ms)),
    dayRanges: instants.map((ms) => chinaDayRange(ms)),
  })
)
"""


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def _instants() -> list[int]:
    return [int(_utc(iso).timestamp() * 1000) for _, iso in FIXTURES]


def _run_node_probe(instants: list[int]) -> dict:
    """把同一份夹具交给 Node 侧的 businessDay.js，拿回它的输出。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("环境里没有 node（CI 的 Python job 不装 Node），跨语言契约只能跳过")

    source = (
        Path(__file__).resolve().parents[1] / "kds" / "utils" / "businessDay.js"
    ).read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        # 原样拷贝源码，不复制逻辑、不改一行：契约测的就是仓库里那份实现
        (tmp / "businessDay.mjs").write_text(source, encoding="utf-8")
        (tmp / "probe.mjs").write_text(_NODE_PROBE_SOURCE, encoding="utf-8")
        completed = subprocess.run(
            [node, str(tmp / "probe.mjs")],
            input=json.dumps({"instants": instants}),
            capture_output=True,
            text=True,
            timeout=60,
        )

    if completed.returncode != 0:
        pytest.fail(
            f"node 探针执行失败（rc={completed.returncode}）：{completed.stderr.strip()}"
        )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def js_side() -> dict:
    result = _run_node_probe(_instants())
    assert len(result["dateKeys"]) == len(FIXTURES), "探针返回的条数必须与夹具一致"
    assert len(result["dayRanges"]) == len(FIXTURES), "探针返回的条数必须与夹具一致"
    return result


def test_js_china_date_key_equals_python_china_calendar_day(js_side) -> None:
    """JS `chinaDateKey()` 必须等于 Python 的东八区日历日（同一组夹具）。"""
    mismatches = []
    for index, (label, iso) in enumerate(FIXTURES):
        expected = to_china_tz(_utc(iso)).date().isoformat()
        actual = js_side["dateKeys"][index]
        if actual != expected:
            mismatches.append(f"{label}({iso}): js={actual} python={expected}")
    assert not mismatches, "JS/Python 的「今天」不一致：\n" + "\n".join(mismatches)


def test_js_day_range_equals_python_china_day_bounds(js_side) -> None:
    """JS `chinaDayRange()` 必须等于 Python 的东八区自然日 [00:00, 23:59:59.999]。"""
    mismatches = []
    for index, (label, iso) in enumerate(FIXTURES):
        moment = to_china_tz(_utc(iso))
        day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        expected_start = day_start.astimezone(timezone.utc)
        expected_end = (
            day_start + timedelta(days=1) - timedelta(milliseconds=1)
        ).astimezone(timezone.utc)

        actual_start = _utc(js_side["dayRanges"][index]["start"])
        actual_end = _utc(js_side["dayRanges"][index]["end"])
        if actual_start != expected_start or actual_end != expected_end:
            mismatches.append(
                f"{label}({iso}): js=[{actual_start.isoformat()}, {actual_end.isoformat()}] "
                f"python=[{expected_start.isoformat()}, {expected_end.isoformat()}]"
            )
    assert not mismatches, "JS/Python 的日窗口不一致：\n" + "\n".join(mismatches)


def test_js_and_python_business_date_agree_once_the_cut_has_passed(js_side) -> None:
    """06:00 起（北京时）JS 的自然日就是 Python 的营业日——两侧必须给同一个日期。"""
    for index, (label, iso) in enumerate(FIXTURES):
        moment = to_china_tz(_utc(iso))
        if moment.hour < BUSINESS_DAY_CUT_HOUR:
            continue
        business_date = business_date_of(moment)
        js_date = js_side["dateKeys"][index]
        assert js_date == business_date, (
            f"{label}({iso})：北京时 {moment.hour} 时已过切点，"
            f"js={js_date} 与 python 营业日 {business_date} 应当一致"
        )


def test_the_00_06_divergence_is_pinned(js_side) -> None:
    """00:00–06:00（北京时）必然差一天：把这条已知差异钉住，而不是让它悄悄漂移。"""
    diverging = []
    for index, (label, iso) in enumerate(FIXTURES):
        moment = to_china_tz(_utc(iso))
        natural_day = moment.date().isoformat()
        business_day = business_date_of(moment)

        assert js_side["dateKeys"][index] == natural_day, (
            f"{label}({iso})：JS 口径变了，chinaDateKey 已不再是中国自然日"
        )

        if moment.hour < BUSINESS_DAY_CUT_HOUR:
            diverging.append(label)
            assert business_day != natural_day, (
                f"{label}({iso})：北京时 {moment.hour} 时属于切点之前，"
                "营业日必须落在前一天（与 JS 的自然日相差一天）"
            )
        else:
            assert business_day == natural_day, (
                f"{label}({iso})：北京时 {moment.hour} 时已过切点，"
                "营业日必须等于当天"
            )

    assert diverging, "夹具必须覆盖 00:00–06:00 这段差异带，否则这条用例什么也没钉住"
    assert set(diverging) == {
        "before-cut",
        "china-midnight",
        "night-shift",
        "month-boundary",
        "year-boundary",
        "leap-day",
    }, f"差异带覆盖发生了变化：{sorted(diverging)}"

    # 具体到秒的边界：切点前后一个毫秒，营业日就换天，而自然日不动
    before_cut = to_china_tz(_utc("2026-09-23T21:59:59.999Z"))
    at_cut = to_china_tz(_utc("2026-09-23T22:00:00.000Z"))
    assert before_cut.date().isoformat() == at_cut.date().isoformat() == "2026-09-24"
    assert business_date_of(before_cut) == "2026-09-23"
    assert business_date_of(at_cut) == "2026-09-24"


def test_fixture_instants_are_utc_and_millisecond_exact() -> None:
    """夹具本身的自检：必须是毫秒精确的 UTC 时间戳，两份实现拿到的是同一组值。"""
    instants = _instants()
    assert len(instants) == len(FIXTURES)
    assert all(isinstance(value, int) for value in instants)
    for value, (label, iso) in zip(instants, FIXTURES):
        assert datetime.fromtimestamp(value / 1000, tz=timezone.utc) == _utc(iso), label
    # 北京时间与 UTC 在同一时刻只差固定 8 小时（1991 年后无夏令时）
    assert datetime(2026, 9, 24, tzinfo=CHINA_TZ).utcoffset() == timedelta(hours=8)
