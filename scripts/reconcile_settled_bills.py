#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已结账单与本地 orders 对账 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from database import DatabaseManager
from scraper.restaurant_scraper import RestaurantScraper
from services.dish_catalog import DishCatalog
from services.reconcile_job import default_biz_date, execute_reconcile

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main_async(args: argparse.Namespace) -> int:
    biz_date = args.date or default_biz_date()
    output_dir = Path(args.output_dir) if args.output_dir else Path(settings.DATABASE_DIR) / "reconcile"

    db = DatabaseManager()
    if not await db.connect():
        logger.error("数据库连接失败")
        return 1

    scraper = RestaurantScraper(DishCatalog(db))
    try:
        result = await execute_reconcile(
            db,
            scraper,
            biz_date,
            fix=args.fix,
            notify=args.notify,
            output_dir=output_dir,
        )
        if not result.get("success"):
            logger.error("%s", result.get("error", "对账失败"))
            return 1

        if args.json:
            print(json.dumps(result.get("result", result), ensure_ascii=False, indent=2))
        else:
            logger.info(
                "对账完成 biz_date=%s missed_qty=%s miss_rate=%s%% fixed=%s",
                biz_date,
                result.get("missed_qty"),
                result.get("miss_rate_pct"),
                result.get("fixed_count", 0),
            )
            logger.info("报告: %s", result.get("report_md"))

        missed_qty = float(result.get("missed_qty") or 0)
        return 0 if missed_qty == 0 else 2
    finally:
        await scraper.close()
        await db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="已结账单与本地 orders 对账")
    parser.add_argument("--date", help="营业日 YYYY-MM-DD，默认当前营业日")
    parser.add_argument("--fix", action="store_true", help="补录漏抓订单")
    parser.add_argument("--notify", action="store_true", help="超阈值时发送企微告警")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON 到 stdout")
    parser.add_argument("--output-dir", help="报告输出目录，默认 data/reconcile")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    exit_code = asyncio.run(main_async(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
