#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 semi_finished_rules 接到 prep_items（迁移 0035 之后的回填）。

做三件事，全部幂等，可重复跑：

1. 补齐「规则在用、备货品主数据里没有」的半成品（spec §4 列的 13 个）。
2. 把两个名字漂移的半成品规则改名对齐已有备货品
   （鱼皇粥料→鱼皇料、姜汁双皮奶→双皮奶）。
3. 回填 `semi_finished_rules.prep_item_id`：先按 (名字, 单位) 精确匹配，
   再按名字兜底（单位不同也算命中，单位本身不改）。

用法：
    # 默认 dry-run：在一个事务里真跑一遍然后回滚，因此报出的数字是准的
    .venv/bin/python scripts/prep_items_backfill.py
    # 真写
    .venv/bin/python scripts/prep_items_backfill.py --apply

安全：脚本会打印实际连的库（脱敏）。DSN 走 `POSTGRES_DSN` 环境变量（优先）或 `.env`。
在真库上跑之前先 dry-run 看一遍；测试库上跑用 `POSTGRES_DSN=... luyun_test_xxx`。
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# 从 scripts/ 直接运行时，仓库根不在 sys.path 上（`config` 在根目录）。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CHINA_TZ = timezone(timedelta(hours=8))

# 规则在用、备货品主数据里没有的半成品。kind 按 category 推断
# （半成品 → made、原料 → bought）；推错可在备货页用「其实是原料/半成品」改。
NEW_PREP_ITEMS = [
    # (item_name, unit, position, category, kind)
    ("牛肉球", "份", "熟笼", "半成品", "made"),
    ("米饭", "碗", "熟笼", "半成品", "made"),
    ("四色饺", "份", "案板", "半成品", "made"),
    ("拉丝餐包", "个", "西饼", "半成品", "made"),
    ("黑炭餐包", "个", "西饼", "半成品", "made"),
    ("杨枝甘露", "碗", "西饼", "半成品", "made"),
    ("炖汤", "份", "熟笼", "半成品", "made"),
    ("牛百叶扎", "份", "熟笼", "半成品", "made"),
    ("烧鹅酥", "份", "西饼", "原料", "bought"),
    ("卤水凤爪", "包", "熟笼", "原料", "bought"),
    ("猪肠粉", "斤", "熟笼", "原料", "bought"),
    ("肉饼", "个", "西饼", "原料", "bought"),
    ("黄皮水", "包", "西饼", "原料", "bought"),
]

# 名字漂移：规则里的名字 → 备货品主数据里的名字。
RENAMES = [
    ("鱼皇粥料", "鱼皇料"),
    ("姜汁双皮奶", "双皮奶"),
]

# 与 services/prep_plan_service.POSITION_STATION_MAP 同口径。
# 只用于给新建的备货品填 station，不修改任何存量行。
POSITION_STATION_MAP = {
    "熟笼": "shulong",
    "熟笼冻品": "shulong",
    "馅档": "shulong",
    "案板": "shulong",
    "西饼": "xibing",
    "西饼凉菜": "xibing",
    "肠粉": "changfen",
    "明档": "mingdang1",
    "明档1": "mingdang1",
    "煎炸": "jianzha",
}

BACKFILL_NOTE = "backfill_from_rules_2026-10"


def mask_dsn(dsn: str) -> str:
    """脱敏打印：不显示密码。"""
    try:
        parts = urlsplit(dsn)
        if parts.password:
            netloc = f"{parts.username}:***@{parts.hostname}"
            if parts.port:
                netloc += f":{parts.port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:  # noqa: BLE001 - 只用于日志，不因脱敏失败而中断
        pass
    return dsn


async def _column_exists(conn, table: str, column: str) -> bool:
    return bool(await conn.fetchval(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = $1 AND column_name = $2
        """,
        table,
        column,
    ))


async def _report(conn) -> dict:
    total = await conn.fetchval("SELECT count(*) FROM semi_finished_rules")
    filled = await conn.fetchval(
        "SELECT count(*) FROM semi_finished_rules WHERE prep_item_id IS NOT NULL"
    )
    missing = await conn.fetch(
        """
        SELECT r.semi_name, r.unit, count(*) AS rules
        FROM semi_finished_rules r
        WHERE r.prep_item_id IS NULL
        GROUP BY r.semi_name, r.unit
        ORDER BY count(*) DESC, r.semi_name
        """
    )
    return {"total": total, "filled": filled, "missing": missing}


async def _fill(conn, dry_run: bool) -> None:
    """① 补备货品 ② 名字对齐 ③ 回填 id。全部在调用方的事务里。"""
    # ---- ① 补齐缺失备货品 --------------------------------------------
    print("① 补齐备货品主数据")
    created = 0
    for item_name, unit, position, category, kind in NEW_PREP_ITEMS:
        # 按名字判重、不看单位：规则写「份」而主数据只有「个」是单位漂移，
        # 再建一个同名不同单位的品会让备货清单出现两条同名行。
        exists = await conn.fetchval(
            "SELECT 1 FROM prep_items WHERE item_name = $1", item_name
        )
        if exists:
            print(f"   = {item_name}（{unit}）已存在，跳过")
            continue
        station = POSITION_STATION_MAP.get(position, "")
        now = datetime.now(CHINA_TZ).isoformat()
        await conn.execute(
            """
            INSERT INTO prep_items
                (item_name, station, position, category, unit,
                 shelf_life_hours, lead_time_hours, min_batch_qty,
                 safety_stock_ratio, active, notes, created_at, updated_at, kind)
            VALUES ($1,$2,$3,$4,$5,24,0,0,0.15,1,$6,$7,$8,$9)
            """,
            item_name,
            station,
            position,
            category,
            unit,
            BACKFILL_NOTE,
            now,
            now,
            kind,
        )
        print(f"   + {item_name}（{unit}）{position} · {category} · {kind}")
        created += 1
    print(f"   共新建 {created} 个")
    print()

    # ---- ② 名字漂移：改规则名，对齐已有备货品 -------------------------
    print("② 名字漂移对齐")
    for old_name, new_name in RENAMES:
        target = await conn.fetchval(
            "SELECT count(*) FROM prep_items WHERE item_name = $1", new_name
        )
        if not target:
            print(f"   ！备货品里没有「{new_name}」，跳过「{old_name}」")
            continue
        affected = await conn.execute(
            "UPDATE semi_finished_rules SET semi_name = $1 WHERE semi_name = $2",
            new_name,
            old_name,
        )
        print(f"   ~ {old_name} → {new_name}（{affected}）")
    print()

    # ---- ③a 精确回填：名字 + 单位 ------------------------------------
    print("③ 回填 prep_item_id")
    status = await conn.execute(
        """
        UPDATE semi_finished_rules r
        SET prep_item_id = p.id
        FROM prep_items p
        WHERE p.item_name = r.semi_name
          AND p.unit = r.unit
          AND r.prep_item_id IS NULL
        """
    )
    print(f"   名字+单位精确命中：{status}")

    # ---- ③b 名字兜底：单位不同也算命中（不改单位） ---------------------
    drift = await conn.fetch(
        """
        SELECT r.semi_name, r.unit AS rule_unit, p.unit AS prep_unit, count(*) AS n
        FROM semi_finished_rules r
        JOIN prep_items p ON p.item_name = r.semi_name AND p.unit <> r.unit
        WHERE r.prep_item_id IS NULL
        GROUP BY 1,2,3
        """
    )
    status = await conn.execute(
        """
        UPDATE semi_finished_rules r
        SET prep_item_id = (
            SELECT p.id FROM prep_items p
            WHERE p.item_name = r.semi_name
            ORDER BY p.id
            LIMIT 1
        )
        WHERE r.prep_item_id IS NULL
          AND EXISTS (SELECT 1 FROM prep_items p WHERE p.item_name = r.semi_name)
        """
    )
    print(f"   仅名字命中（单位不同）：{status}")
    for row in drift:
        print(f"     {row['semi_name']}：规则「{row['rule_unit']}」 vs 主数据「{row['prep_unit']}」（{row['n']} 条）")
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description="回填 semi_finished_rules.prep_item_id")
    parser.add_argument("--apply", action="store_true", help="真的写库（默认事务跑一遍再回滚）")
    args = parser.parse_args()
    apply = args.apply

    from config import settings  # 延迟导入：确保 POSTGRES_DSN 环境变量先生效

    dsn = settings.POSTGRES_DSN
    print(f"库：{mask_dsn(dsn)}")
    print(f"模式：{'APPLY（写库）' if apply else 'DRY-RUN（事务内真跑，结束回滚）'}")
    print()

    import asyncpg

    conn = await asyncpg.connect(dsn)
    tx = conn.transaction()
    await tx.start()
    code = 0
    try:
        if not await _column_exists(conn, "semi_finished_rules", "prep_item_id"):
            print("！semi_finished_rules 没有 prep_item_id 列。先应用 migrations/pg/0035_*.sql。")
            return 2

        before = await _report(conn)
        print(f"开始前：规则 {before['total']} 条，已回填 {before['filled']} 条")
        print()

        await _fill(conn, dry_run=not apply)

        after = await _report(conn)
        print(f"结果：规则 {after['total']} 条，已回填 {after['filled']} 条，未回填 {len(after['missing'])} 组")
        if after["missing"]:
            print("仍未回填（需要人工处理）：")
            for row in after["missing"]:
                print(f"  - {row['semi_name']}（{row['unit']}）{row['rules']} 条")
            code = 1
        return code
    finally:
        if apply:
            await tx.commit()
            print("已提交。")
        else:
            await tx.rollback()
            print("已回滚（DRY-RUN 没有写库）。确认无误后加 --apply。")
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
