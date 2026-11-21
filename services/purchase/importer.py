#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""worktool2 云开发导出 JSON → luyun 厨房采购的迁移器。

同一套逻辑有两个入口：

- 命令行：scripts/import_worktool2_purchase.py（一次性搬迁、演练、写报告）。
- 网页：services/purchase/migration.py 调用 import_source，把上传的文件先跑 dry-run
  预览，再由管理员确认 apply。两边共用这里的映射、幂等规则与报告。

源目录放云开发控制台导出的 8 个集合：
suppliers / categories / products / positions / date_factor_tags /
date_factor_weekday_defaults / orders / material_stock。
支持 .json（数组或 {data:[...]}）与 .jsonl / .ndjson；集合名也可带日期后缀。

设计要点
--------
- 只写 purchase_* 十二张表，不碰任何既有表；tenant_id 固定 1（单店口径）。
- 订单项与盘点里的 productId 是云开发 products._id，不是 7 位商品编码；脚本先用
  _id 建映射，再落到目标 product_id。找不到商品的订单项保留快照、product_id 置空；
  找不到商品的盘点行跳过并计入报告。
- 订单号由云 _id 的 CRC32 生成（PYYYYMMDD-XXXXXX），重跑同一条云订单得到同一个
  单号，因此不会重复导入；新系统自己的单仍是 PYYYYMMDD-0001 顺序号。
- 老订单可能跨多个岗位；脚本取出现次数最多的岗位作为整单岗位，跨岗位单在报告里
  记为 warning，不拆单（拆单会改变订单数）。
- 员工映射尽力而为：按 userInfo 里的姓名在 hygiene_employees 找唯一同名员工；找不到
  或重名就留空 submitter_employee_id（分析按姓名去重，见 spec 已知限制 7）。
- 逐单提交、可断点续跑；失败不会回滚已成功的单，报告里列失败原因。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import gzip
import json
import os
import re
import sys
import traceback
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "scripts" / "migrate" / "data" / "worktool2"
DEFAULT_DSN = os.environ.get("POSTGRES_DSN") or "postgresql://localhost:5432/luyun"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))
TENANT_ID = 1

COLLECTIONS = (
    "suppliers",
    "categories",
    "products",
    "positions",
    "date_factor_tags",
    "date_factor_weekday_defaults",
    "orders",
    "material_stock",
)

TAGS = {"2万", "3万", "4万", "5万", "6万", "7万", "8万"}
POSITION_SPLIT_RE = re.compile(r"[、,，/／;；|]+")


class ImportSourceError(ValueError):
    """上传目录不可读、没有集合、或目标库缺少采购表。"""


class ImportSchemaError(ImportSourceError):
    """目标库还没应用 migrations/pg/0062_kitchen_purchase.sql。"""


def redact_dsn(dsn: str) -> str:
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", dsn)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def as_text(value: Any, default: str = "") -> str:
    if _blank(value):
        return default
    return str(value).strip()


def as_number(value: Any) -> Optional[float]:
    if _blank(value):
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_bool(value: Any, default: bool = False) -> bool:
    if _blank(value):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "需要", "启用", "启用中"}:
        return True
    if text in {"0", "false", "no", "n", "否", "不需要", "停用", "禁用"}:
        return False
    return default


def as_day(value: Any) -> Optional[dt.date]:
    """把云导出里的日期（字符串 / Date 扩展 / 时间戳）压成北京自然日。"""
    moment = as_datetime(value)
    return moment.date() if moment else None


def as_datetime(value: Any) -> Optional[dt.datetime]:
    if _blank(value):
        return None
    if isinstance(value, dt.datetime):
        moment = value
    elif isinstance(value, dt.date):
        moment = dt.datetime.combine(value, dt.time(12, 0))
    elif isinstance(value, dict):
        for key in ("$date", "date", "value", "time", "$numberLong", "seconds"):
            if key in value:
                moment = as_datetime(value[key])
                if moment is not None:
                    return moment
        return None
    elif isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000.0
        try:
            moment = dt.datetime.fromtimestamp(number, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = dt.datetime.fromisoformat(text)
        except ValueError:
            moment = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    moment = dt.datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if moment is None:
                return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CHINA_TZ)
    return moment.astimezone(CHINA_TZ)


def position_names(value: Any) -> List[str]:
    if _blank(value):
        return []
    if isinstance(value, (list, tuple, set)):
        names: List[str] = []
        for item in value:
            names.extend(position_names(item))
        return names
    text = as_text(value)
    return [part.strip() for part in POSITION_SPLIT_RE.split(text) if part.strip()]


def normalize_code(value: Any, width: int) -> Optional[str]:
    text = as_text(value)
    if not text:
        return None
    if text.isdigit():
        return text.zfill(width)
    return None


def normalize_product_code(value: Any) -> Optional[str]:
    return normalize_code(value, 7)


def order_no_for(cloud_id: str, submitted: dt.datetime) -> str:
    crc = zlib.crc32(cloud_id.encode("utf-8")) & 0xFFFFFFFF
    return "P%s-%06d" % (submitted.strftime("%Y%m%d"), crc % 1_000_000)


def _parse_concatenated_json(text: str) -> List[Dict[str, Any]]:
    """解析首尾相接的 JSON 对象（云开发导出的 .json 实际是 JSON Lines）。"""
    decoder = json.JSONDecoder()
    rows: List[Dict[str, Any]] = []
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        value, index = decoder.raw_decode(text, index)
        if not isinstance(value, dict):
            raise ValueError("集合文件里每一项都必须是对象")
        rows.append(value)
    return rows


def load_documents(path: Path) -> List[Dict[str, Any]]:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    text = raw.decode("utf-8-sig")
    if path.suffix.lower() in {".jsonl", ".ndjson"} or path.name.lower().endswith((".jsonl.gz", ".ndjson.gz")):
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # 腾讯云开发导出的 .json 是每行一个对象，不是数组；文件后缀也不会带 .jsonl。
        return _parse_concatenated_json(text)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # 包装形状只认明确的集合键；不认 items，订单文档本身就有 items 数组。
        for key in ("data", "list", "records", "results", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for inner in ("list", "records", "items", "rows"):
                    if isinstance(value.get(inner), list):
                        return value[inner]
        # 单对象也可能是合法的集合（例如只导出一条订单）；不能把它的 items 当集合。
        return [payload]
    raise ValueError("认不出这个 JSON 的形状：需要数组，或 {data:[...]}")


def discover_files(source: Path) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    candidates = sorted(source.glob("*")) if source.is_dir() else [source]
    for path in candidates:
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("."):
            continue
        for collection in COLLECTIONS:
            if name == collection + ".json" or name.startswith(collection + ".") or name.startswith(collection + "-"):
                found.setdefault(collection, path)
                break
    return found


@dataclass
class Stats:
    source: int = 0
    created: int = 0
    updated: int = 0
    existing: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "source": self.source,
            "created": self.created,
            "updated": self.updated,
            "existing": self.existing,
            "skipped": self.skipped,
            "failed": self.failed,
        }


@dataclass
class Report:
    source_dir: str
    apply: bool
    collections: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    stats: Dict[str, Stats] = field(default_factory=dict)
    orders: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        if len(self.warnings) < 500:
            self.warnings.append(message)
        elif len(self.warnings) == 500:
            self.warnings.append("（warning 超过 500 条，后续省略）")

    def fail(self, message: str) -> None:
        if len(self.failures) < 500:
            self.failures.append(message)
        elif len(self.failures) == 500:
            self.failures.append("（failure 超过 500 条，后续省略）")

    def stat(self, name: str) -> Stats:
        if name not in self.stats:
            self.stats[name] = Stats()
        return self.stats[name]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_dir": self.source_dir,
            "apply": self.apply,
            "collections": self.collections,
            "stats": {name: stat.as_dict() for name, stat in self.stats.items()},
            "orders": self.orders,
            "warnings": self.warnings,
            "failures": self.failures,
        }


class ImportRunner:
    def __init__(self, conn: asyncpg.Connection, report: Report, *, apply: bool, sync_catalog: bool) -> None:
        self.conn = conn
        self.report = report
        self.apply = apply
        self.sync_catalog = sync_catalog
        self._fake_id = -1
        self.supplier_by_name: Dict[str, int] = {}
        self.supplier_by_cloud: Dict[str, int] = {}
        self.supplier_codes: Dict[int, str] = {}
        self.category_by_key: Dict[Tuple[int, str], int] = {}
        self.category_by_cloud: Dict[str, int] = {}
        self.category_codes: Dict[int, str] = {}
        self.position_by_name: Dict[str, int] = {}
        self.product_by_code: Dict[str, Dict[str, Any]] = {}
        self.product_by_cloud: Dict[str, Dict[str, Any]] = {}
        self.employee_by_name: Optional[Dict[str, List[int]]] = None
        self._supplier_next: Dict[int, int] = {}
        self._category_next: Dict[int, int] = {}
        self._multi_position_orders = 0
        self._unknown_item_products = 0
        self._unknown_stock_products = 0
        self._order_items = 0
        self._order_quantity = 0.0

    # ---- 基础 -----------------------------------------------------

    def fake_id(self) -> int:
        value = self._fake_id
        self._fake_id -= 1
        return value

    async def _fetchrow(self, sql: str, *args: Any) -> Optional[asyncpg.Record]:
        return await self.conn.fetchrow(sql, *args)

    async def _fetch(self, sql: str, *args: Any) -> List[asyncpg.Record]:
        return await self.conn.fetch(sql, *args)

    async def _execute(self, sql: str, *args: Any) -> str:
        return await self.conn.execute(sql, *args)

    async def check_schema(self) -> None:
        row = await self._fetchrow("SELECT to_regclass('public.purchase_products') AS table_name")
        if row is None or row["table_name"] is None:
            raise ImportSchemaError(
                "目标库还没建采购表：先应用 migrations/pg/0062_kitchen_purchase.sql"
                "（Admin「系统更新 → 数据库迁移」，或 psql -f）。"
            )

    async def _next_supplier_code(self) -> str:
        rows = await self._fetch(
            "SELECT code FROM purchase_suppliers "
            "WHERE tenant_id = $1 AND code ~ '^[0-9]{2}$'",
            TENANT_ID,
        )
        used = [int(row["code"]) for row in rows]
        value = (max(used) + 1) if used else 1
        if value > 99:
            raise ValueError("供应商编码 00-99 用完了")
        return "%02d" % value

    async def _next_category_code(self, supplier_id: int) -> str:
        if supplier_id < 0:
            value = self._supplier_next.get(supplier_id, 1)
            self._supplier_next[supplier_id] = value + 1
            return "%02d" % value
        if self.apply:
            row = await self._fetchrow(
                "UPDATE purchase_suppliers SET next_category_code = next_category_code + 1 "
                "WHERE id = $1 RETURNING next_category_code",
                supplier_id,
            )
            value = int(row["next_category_code"]) - 1 if row else 1
        else:
            row = await self._fetchrow(
                "SELECT next_category_code FROM purchase_suppliers WHERE id = $1",
                supplier_id,
            )
            value = int(row["next_category_code"]) if row else 1
            self._supplier_next[supplier_id] = value + 1
        if value > 99:
            raise ValueError("分类编码 00-99 用完了（supplier_id=%s）" % supplier_id)
        return "%02d" % value

    async def _next_product_seq(self, category_id: int) -> int:
        if category_id < 0:
            value = self._category_next.get(category_id, 1)
            self._category_next[category_id] = value + 1
            return value
        if self.apply:
            row = await self._fetchrow(
                "UPDATE purchase_categories SET next_product_seq = next_product_seq + 1 "
                "WHERE id = $1 RETURNING next_product_seq",
                category_id,
            )
            return int(row["next_product_seq"]) - 1 if row else 1
        row = await self._fetchrow(
            "SELECT next_product_seq FROM purchase_categories WHERE id = $1",
            category_id,
        )
        value = int(row["next_product_seq"]) if row else 1
        self._category_next[category_id] = value + 1
        return value

    # ---- 岗位 -----------------------------------------------------

    async def ensure_position(self, name: str, *, sort_order: int = 900, show_in_order: bool = False) -> int:
        name = as_text(name) or "未分组"
        existing = self.position_by_name.get(name)
        if existing is not None:
            return existing
        row = await self._fetchrow(
            "SELECT id FROM purchase_positions WHERE tenant_id = $1 AND name = $2",
            TENANT_ID, name,
        )
        if row:
            self.position_by_name[name] = int(row["id"])
            return int(row["id"])
        if not self.apply:
            value = self.fake_id()
            self.position_by_name[name] = value
            return value
        row = await self._fetchrow(
            "INSERT INTO purchase_positions (tenant_id, name, sort_order, show_in_order, active) "
            "VALUES ($1, $2, $3, $4, TRUE) RETURNING id",
            TENANT_ID, name, sort_order, show_in_order,
        )
        self.position_by_name[name] = int(row["id"])
        return int(row["id"])

    async def import_positions(self, docs: List[Dict[str, Any]]) -> None:
        stat = self.report.stat("positions")
        stat.source = len(docs)
        for index, doc in enumerate(docs):
            name = as_text(doc.get("name") or doc.get("positionName") or doc.get("title"))
            if not name:
                stat.skipped += 1
                self.report.warn("positions 第 %s 条没有 name，已跳过" % (index + 1))
                continue
            before = name in self.position_by_name
            await self.ensure_position(name, sort_order=880, show_in_order=True)
            if before:
                stat.existing += 1
            else:
                stat.created += 1

    # ---- 供应商 / 分类 --------------------------------------------

    async def ensure_supplier(self, name: str, *, code: Optional[str] = None, active: bool = True, cloud_id: str = "", next_category_code: Optional[int] = None) -> int:
        name = as_text(name) or "未分类"
        code = normalize_code(code, 2)
        existing = self.supplier_by_name.get(name)
        if existing is not None:
            if cloud_id:
                self.supplier_by_cloud[cloud_id] = existing
            return existing
        row = await self._fetchrow(
            "SELECT id, name, code FROM purchase_suppliers WHERE tenant_id = $1 AND name = $2",
            TENANT_ID, name,
        )
        if row is None and code:
            row = await self._fetchrow(
                "SELECT id, name, code FROM purchase_suppliers WHERE tenant_id = $1 AND code = $2",
                TENANT_ID, code,
            )
        if row:
            target_id = int(row["id"])
            self.supplier_by_name[name] = target_id
            self.supplier_by_name[as_text(row["name"])] = target_id
            self.supplier_codes[target_id] = as_text(row["code"])
            if cloud_id:
                self.supplier_by_cloud[cloud_id] = target_id
            return target_id
        if not code:
            code = await self._next_supplier_code()
        if not self.apply:
            target_id = self.fake_id()
            self._supplier_next[target_id] = int(next_category_code or 1)
        else:
            row = await self._fetchrow(
                "INSERT INTO purchase_suppliers (tenant_id, name, code, next_category_code, active) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                TENANT_ID, name, code, int(next_category_code or 1), active,
            )
            target_id = int(row["id"])
        self.supplier_codes[target_id] = code
        self.supplier_by_name[name] = target_id
        if cloud_id:
            self.supplier_by_cloud[cloud_id] = target_id
        return target_id

    async def import_suppliers(self, docs: List[Dict[str, Any]]) -> None:
        stat = self.report.stat("suppliers")
        stat.source = len(docs)
        for index, doc in enumerate(docs):
            name = as_text(doc.get("name") or doc.get("supplierName"))
            if not name:
                stat.skipped += 1
                self.report.warn("suppliers 第 %s 条没有 name，已跳过" % (index + 1))
                continue
            before = name in self.supplier_by_name
            await self.ensure_supplier(
                name,
                code=doc.get("code"),
                active=as_bool(doc.get("isActive", doc.get("active")), True),
                cloud_id=as_text(doc.get("_id")),
                next_category_code=doc.get("nextCategoryCode"),
            )
            if before:
                stat.existing += 1
            else:
                stat.created += 1

    async def ensure_category(self, supplier_id: int, name: str, *, code: Optional[str] = None, active: bool = True, cloud_id: str = "", next_product_seq: Optional[int] = None) -> int:
        name = as_text(name) or "未分类"
        code = normalize_code(code, 2)
        key = (supplier_id, name)
        existing = self.category_by_key.get(key)
        if existing is not None:
            if cloud_id:
                self.category_by_cloud[cloud_id] = existing
            return existing
        row = await self._fetchrow(
            "SELECT id, name, code FROM purchase_categories WHERE supplier_id = $1 AND name = $2",
            supplier_id, name,
        )
        if row is None and code:
            row = await self._fetchrow(
                "SELECT id, name, code FROM purchase_categories WHERE supplier_id = $1 AND code = $2",
                supplier_id, code,
            )
        if row:
            target_id = int(row["id"])
            self.category_by_key[key] = target_id
            self.category_codes[target_id] = as_text(row["code"])
            if cloud_id:
                self.category_by_cloud[cloud_id] = target_id
            return target_id
        if not code:
            code = await self._next_category_code(supplier_id)
        if not self.apply:
            target_id = self.fake_id()
            self._category_next[target_id] = int(next_product_seq or 1)
        else:
            row = await self._fetchrow(
                "INSERT INTO purchase_categories (supplier_id, name, code, next_product_seq, active) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                supplier_id, name, code, int(next_product_seq or 1), active,
            )
            target_id = int(row["id"])
        self.category_codes[target_id] = code
        self.category_by_key[key] = target_id
        if cloud_id:
            self.category_by_cloud[cloud_id] = target_id
        return target_id

    async def import_categories(self, docs: List[Dict[str, Any]]) -> None:
        stat = self.report.stat("categories")
        stat.source = len(docs)
        for index, doc in enumerate(docs):
            name = as_text(doc.get("name") or doc.get("categoryName"))
            if not name:
                stat.skipped += 1
                self.report.warn("categories 第 %s 条没有 name，已跳过" % (index + 1))
                continue
            supplier_cloud = as_text(doc.get("supplierId") or doc.get("supplier_id"))
            supplier_name = as_text(doc.get("supplierName") or doc.get("supplier"))
            supplier_id = self.supplier_by_cloud.get(supplier_cloud) if supplier_cloud else None
            if supplier_id is None and supplier_name:
                supplier_id = self.supplier_by_name.get(supplier_name)
            if supplier_id is None:
                stat.skipped += 1
                self.report.warn("categories 第 %s 条找不到供应商（%s），已跳过" % (index + 1, supplier_name or supplier_cloud or "空"))
                continue
            key = (supplier_id, name)
            before = key in self.category_by_key
            await self.ensure_category(
                supplier_id, name,
                code=doc.get("code"),
                active=as_bool(doc.get("isActive", doc.get("active")), True),
                cloud_id=as_text(doc.get("_id")),
                next_product_seq=doc.get("nextProductSeq"),
            )
            if before:
                stat.existing += 1
            else:
                stat.created += 1

    # ---- 商品 -----------------------------------------------------

    async def generate_product_code(self, supplier_id: int, category_id: int) -> str:
        supplier_code = self.supplier_codes.get(supplier_id)
        if supplier_code is None and supplier_id >= 0:
            row = await self._fetchrow("SELECT code FROM purchase_suppliers WHERE id = $1", supplier_id)
            supplier_code = str(row["code"]) if row else None
        category_code = self.category_codes.get(category_id)
        if category_code is None and category_id >= 0:
            row = await self._fetchrow("SELECT code FROM purchase_categories WHERE id = $1", category_id)
            category_code = str(row["code"]) if row else None
        if not supplier_code or not category_code:
            raise ValueError("生成商品编码时找不到供应商或分类")
        seq = await self._next_product_seq(category_id)
        if seq > 999:
            raise ValueError("商品序号 001-999 用完了（category_id=%s）" % category_id)
        return "%s%s%03d" % (supplier_code, category_code, seq)

    async def import_products(self, docs: List[Dict[str, Any]]) -> None:
        stat = self.report.stat("products")
        stat.source = len(docs)
        for index, doc in enumerate(docs):
            cloud_id = as_text(doc.get("_id"))
            try:
                await self._import_product(doc, index, stat)
            except Exception as exc:  # noqa: BLE001 —— 单条失败继续
                stat.failed += 1
                stat.skipped += 1
                self.report.fail("products 第 %s 条（_id=%s）失败：%s" % (index + 1, cloud_id, exc))

    async def _import_product(self, doc: Dict[str, Any], index: int, stat: Stats) -> None:
        cloud_id = as_text(doc.get("_id"))
        name = as_text(doc.get("name") or doc.get("productName"))
        if not name:
            raise ValueError("没有商品名称")
        supplier_name = as_text(doc.get("supplier") or doc.get("supplierName")) or "未分类"
        supplier_id = self.supplier_by_name.get(supplier_name)
        if supplier_id is None:
            supplier_id = await self.ensure_supplier(supplier_name, code=doc.get("supplierCode"))
        category_cloud = as_text(doc.get("categoryId") or doc.get("category_id"))
        category_name = as_text(doc.get("categoryName") or doc.get("category")) or "未分类"
        category_id = self.category_by_cloud.get(category_cloud) if category_cloud else None
        if category_id is None:
            category_id = self.category_by_key.get((supplier_id, category_name))
        if category_id is None:
            category_id = await self.ensure_category(supplier_id, category_name, code=doc.get("categoryCode"))
        requested_code = normalize_product_code(doc.get("productId") or doc.get("productCode") or doc.get("code"))
        existing = None
        if requested_code:
            existing = self.product_by_code.get(requested_code)
            if existing is None:
                row = await self._fetchrow(
                    "SELECT id, product_code, name, supplier_id, category_id, order_step, specification, unit "
                    "FROM purchase_products WHERE tenant_id = $1 AND product_code = $2",
                    TENANT_ID, requested_code,
                )
                if row:
                    existing = {
                        "id": int(row["id"]),
                        "product_code": str(row["product_code"]),
                        "name": str(row["name"]),
                        "supplier_id": int(row["supplier_id"]) if row["supplier_id"] is not None else supplier_id,
                        "category_id": int(row["category_id"]) if row["category_id"] is not None else category_id,
                        "order_step": float(row["order_step"] or 1),
                        "specification": as_text(row["specification"]),
                        "unit": as_text(row["unit"]),
                        "supplier_name": supplier_name,
                        "category_name": category_name,
                    }
                    self.product_by_code[requested_code] = existing
        if existing is None and not requested_code:
            row = await self._fetchrow(
                "SELECT id, product_code, name, supplier_id, category_id, order_step, specification, unit "
                "FROM purchase_products WHERE tenant_id = $1 AND supplier_id = $2 AND name = $3 "
                "AND specification = $4 AND unit = $5 ORDER BY id LIMIT 1",
                TENANT_ID, supplier_id, name, as_text(doc.get("specification")), as_text(doc.get("unit"), "个"),
            )
            if row:
                existing = {
                    "id": int(row["id"]),
                    "product_code": str(row["product_code"]),
                    "name": str(row["name"]),
                    "supplier_id": int(row["supplier_id"]) if row["supplier_id"] is not None else supplier_id,
                    "category_id": int(row["category_id"]) if row["category_id"] is not None else category_id,
                    "order_step": float(row["order_step"] or 1),
                    "specification": as_text(row["specification"]),
                    "unit": as_text(row["unit"]),
                    "supplier_name": supplier_name,
                    "category_name": category_name,
                }
                self.product_by_code[str(row["product_code"])] = existing
        if existing:
            stat.existing += 1
            if self.apply and self.sync_catalog:
                await self._update_product(int(existing["id"]), doc, supplier_id, category_id)
                existing.update({
                    "name": as_text(doc.get("name") or doc.get("productName"), existing["name"]),
                    "supplier_id": supplier_id,
                    "category_id": category_id,
                    "order_step": float(self._product_values("", doc, supplier_id, category_id)["order_step"]),
                    "supplier_name": supplier_name,
                    "category_name": category_name,
                })
                stat.updated += 1
            self.product_by_cloud[cloud_id] = existing
            await self._set_product_positions(int(existing["id"]), doc, replace=True)
            return
        code = requested_code
        if not code:
            code = await self.generate_product_code(supplier_id, category_id)
            if code in self.product_by_code:
                raise ValueError("生成的商品编码 %s 已存在" % code)
        values = self._product_values(code, doc, supplier_id, category_id)
        if not self.apply:
            target_id = self.fake_id()
            stat.created += 1
            record = {"id": target_id, "product_code": code, "supplier_name": supplier_name, "category_name": category_name, **values}
        else:
            row = await self._fetchrow(
                "INSERT INTO purchase_products (tenant_id, product_code, supplier_id, category_id, name, "
                "specification, unit, remark, order_step, safety_factor, need_stock_check, is_long_cycle, active) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) RETURNING id",
                TENANT_ID, code, supplier_id, category_id, values["name"], values["specification"],
                values["unit"], values["remark"], values["order_step"], values["safety_factor"],
                values["need_stock_check"], values["is_long_cycle"], values["active"],
            )
            target_id = int(row["id"])
            stat.created += 1
            record = {"id": target_id, "product_code": code, "supplier_name": supplier_name, "category_name": category_name, **values}
        self.product_by_code[code] = record
        self.product_by_cloud[cloud_id] = record
        await self._set_product_positions(target_id, doc)

    def _product_values(self, code: str, doc: Dict[str, Any], supplier_id: int, category_id: int) -> Dict[str, Any]:
        order_step = as_number(doc.get("orderStep") or doc.get("order_step"))
        safety = as_number(doc.get("safetyFactor") or doc.get("safety_factor"))
        return {
            "name": as_text(doc.get("name") or doc.get("productName")),
            "specification": as_text(doc.get("specification")),
            "unit": as_text(doc.get("unit"), "个"),
            "remark": as_text(doc.get("remark")),
            "order_step": order_step if order_step and order_step > 0 else 1.0,
            "safety_factor": safety if safety and safety > 0 else 1.2,
            "need_stock_check": as_bool(doc.get("needStockCheck"), False),
            "is_long_cycle": as_bool(doc.get("isLongCycle"), False),
            "active": as_bool(doc.get("isActive", doc.get("active")), True),
            "supplier_id": supplier_id,
            "category_id": category_id,
        }

    async def _update_product(self, product_id: int, doc: Dict[str, Any], supplier_id: int, category_id: int) -> None:
        values = self._product_values("", doc, supplier_id, category_id)
        await self._execute(
            "UPDATE purchase_products SET supplier_id = $1, category_id = $2, name = $3, "
            "specification = $4, unit = $5, remark = $6, order_step = $7, safety_factor = $8, "
            "need_stock_check = $9, is_long_cycle = $10, active = $11, updated_at = NOW() WHERE id = $12",
            supplier_id, category_id, values["name"], values["specification"], values["unit"],
            values["remark"], values["order_step"], values["safety_factor"],
            values["need_stock_check"], values["is_long_cycle"], values["active"], product_id,
        )

    async def _set_product_positions(self, product_id: int, doc: Dict[str, Any], *, replace: bool = False) -> None:
        names = position_names(doc.get("position") or doc.get("positions"))
        if self.apply and replace:
            await self._execute("DELETE FROM purchase_product_positions WHERE product_id = $1", product_id)
        for name in names:
            position_id = await self.ensure_position(name, sort_order=880, show_in_order=True)
            if not self.apply:
                continue
            await self._execute(
                "INSERT INTO purchase_product_positions (product_id, position_id) VALUES ($1, $2) "
                "ON CONFLICT (product_id, position_id) DO NOTHING",
                product_id, position_id,
            )

    # ---- 标签 -----------------------------------------------------

    async def import_date_tags(self, docs: List[Dict[str, Any]]) -> None:
        stat = self.report.stat("date_factor_tags")
        stat.source = len(docs)
        for index, doc in enumerate(docs):
            day = as_day(doc.get("date") or doc.get("tag_date"))
            tag = as_text(doc.get("tag"))
            if day is None or tag not in TAGS:
                stat.skipped += 1
                self.report.warn("date_factor_tags 第 %s 条日期或标签无效，已跳过" % (index + 1))
                continue
            if not self.apply:
                stat.created += 1
                continue
            await self._execute(
                "INSERT INTO purchase_date_tags (tenant_id, tag_date, tag, updated_by) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT (tenant_id, tag_date) "
                "DO UPDATE SET tag = EXCLUDED.tag, updated_by = EXCLUDED.updated_by, updated_at = NOW()",
                TENANT_ID, day, tag, "导入",
            )
            stat.created += 1

    async def import_weekday_defaults(self, docs: List[Dict[str, Any]]) -> None:
        stat = self.report.stat("date_factor_weekday_defaults")
        stat.source = len(docs)
        for index, doc in enumerate(docs):
            try:
                weekday = int(doc.get("weekday"))
            except (TypeError, ValueError):
                weekday = -1
            tag = as_text(doc.get("tag"))
            if weekday < 0 or weekday > 6 or tag not in TAGS:
                stat.skipped += 1
                self.report.warn("date_factor_weekday_defaults 第 %s 条无效，已跳过" % (index + 1))
                continue
            if not self.apply:
                stat.created += 1
                continue
            await self._execute(
                "INSERT INTO purchase_weekday_defaults (tenant_id, weekday, tag) VALUES ($1, $2, $3) "
                "ON CONFLICT (tenant_id, weekday) DO UPDATE SET tag = EXCLUDED.tag, updated_at = NOW()",
                TENANT_ID, weekday, tag,
            )
            stat.created += 1

    # ---- 订单 -----------------------------------------------------

    def _employee_ids(self, name: str) -> List[int]:
        if self.employee_by_name is None:
            self.employee_by_name = {}
        return self.employee_by_name.get(name, [])

    async def load_employees(self) -> None:
        self.employee_by_name = {}
        try:
            rows = await self._fetch("SELECT id, name FROM hygiene_employees")
        except asyncpg.UndefinedTableError:
            return
        for row in rows:
            name = as_text(row["name"])
            if name:
                self.employee_by_name.setdefault(name, []).append(int(row["id"]))

    def _submitter(self, doc: Dict[str, Any]) -> Tuple[Optional[int], str]:
        user = doc.get("userInfo") or doc.get("user") or {}
        if not isinstance(user, dict):
            user = {}
        name = as_text(
            user.get("name")
            or user.get("nickName")
            or user.get("realName")
            or user.get("userName")
            or doc.get("userName")
            or doc.get("submitterName")
        ) or "未知用户"
        raw_id = user.get("employeeId") or user.get("employee_id")
        employee_id = int(raw_id) if str(raw_id or "").isdigit() else None
        if employee_id is None:
            matches = self._employee_ids(name)
            if len(matches) == 1:
                employee_id = matches[0]
        return employee_id, name

    def _order_position(self, doc: Dict[str, Any], items: List[Dict[str, Any]]) -> str:
        direct = as_text(doc.get("positionName") or doc.get("position"))
        if direct:
            return direct
        counter: Counter[str] = Counter()
        for item in items:
            for name in position_names(item.get("position")):
                counter[name] += 1
        if not counter:
            return "未分组"
        return counter.most_common(1)[0][0]

    def _order_status(self, doc: Dict[str, Any]) -> str:
        status = as_text(doc.get("status"), "pending").lower()
        if status in {"completed", "done"}:
            return "completed"
        if status in {"cancelled", "canceled"}:
            return "cancelled"
        return "pending"

    async def import_orders(self, docs: List[Dict[str, Any]]) -> None:
        stat = self.report.stat("orders")
        stat.source = len(docs)
        await self.load_employees()
        for index, doc in enumerate(docs):
            try:
                await self._import_order(doc, index, stat)
            except Exception as exc:  # noqa: BLE001
                stat.failed += 1
                self.report.fail("orders 第 %s 条（_id=%s）失败：%s" % (index + 1, as_text(doc.get("_id")), exc))

    async def _import_order(self, doc: Dict[str, Any], index: int, stat: Stats) -> None:
        cloud_id = as_text(doc.get("_id")) or "row-%s" % (index + 1)
        raw_items = doc.get("items")
        items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        submitted = as_datetime(doc.get("createTime") or doc.get("create_time") or doc.get("submittedAt"))
        if submitted is None or not items:
            stat.skipped += 1
            self.report.warn("orders 第 %s 条没有有效 createTime 或 items，已跳过" % (index + 1))
            return
        item_positions = set()
        for item in items:
            item_positions.update(position_names(item.get("position")))
        if len(item_positions) > 1:
            self._multi_position_orders += 1
        position_name = self._order_position(doc, items)
        position_id = await self.ensure_position(position_name)
        employee_id, submitter_name = self._submitter(doc)
        status = self._order_status(doc)
        order_no = order_no_for(cloud_id, submitted)
        existing = await self._fetchrow(
            "SELECT id FROM purchase_orders WHERE tenant_id = $1 AND order_no = $2",
            TENANT_ID, order_no,
        )
        if existing:
            stat.existing += 1
            return
        if not self.apply:
            stat.created += 1
            self._order_items += len(items)
            self._order_quantity += sum(as_number(item.get("quantity")) or 0 for item in items)
            return
        row = await self._fetchrow(
            "INSERT INTO purchase_orders (tenant_id, order_no, position_id, position_name, "
            "submitter_employee_id, submitter_name, submitter_kind, status, submitted_at, updated_at, updated_by) "
            "VALUES ($1,$2,$3,$4,$5,$6,'staff',$7,$8,NOW(),$9) RETURNING id",
            TENANT_ID, order_no, position_id, position_name, employee_id, submitter_name,
            status, submitted, "导入",
        )
        order_id = int(row["id"])
        total_quantity = 0.0
        for sort_order, item in enumerate(items):
            quantity = as_number(item.get("quantity"))
            if quantity is None or quantity <= 0:
                continue
            cloud_product_id = as_text(item.get("productId") or item.get("product_id") or item.get("productID"))
            product = self.product_by_cloud.get(cloud_product_id) if cloud_product_id else None
            if product is None:
                self._unknown_item_products += 1
                product_id = None
                product_code = cloud_product_id or as_text(item.get("productCode")) or "UNKNOWN"
                supplier_id = None
                supplier_name = as_text(item.get("supplier"))
                category_name = ""
                order_step = 1.0
            else:
                product_id = int(product["id"])
                product_code = str(product.get("product_code") or cloud_product_id)
                supplier_id = int(product.get("supplier_id") or 0) or None
                supplier_name = as_text(item.get("supplier")) or as_text(product.get("supplier_name"))
                category_name = as_text(product.get("category_name"))
                order_step = float(product.get("order_step") or 1)
            item_position = "、".join(position_names(item.get("position"))) or position_name
            await self._execute(
                "INSERT INTO purchase_order_items (order_id, product_id, product_code, name, specification, unit, "
                "supplier_id, supplier_name, category_name, position_name, quantity, order_step, sort_order) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
                order_id, product_id, product_code,
                as_text(item.get("name"), "未知商品"), as_text(item.get("specification")),
                as_text(item.get("unit"), "个"), supplier_id, supplier_name, category_name,
                item_position, float(quantity), order_step, sort_order,
            )
            total_quantity += float(quantity)
        details = {
            "source": "worktool2",
            "cloud_id": cloud_id,
            "status": status,
            "item_count": len(items),
        }
        await self._execute(
            "INSERT INTO purchase_order_events (order_id, kind, actor_name, actor_kind, detail_json, created_at) "
            "VALUES ($1, 'submitted', $2, 'staff', $3::jsonb, $4)",
            order_id, submitter_name, json.dumps(details, ensure_ascii=False), submitted,
        )
        if status == "completed":
            await self._execute(
                "INSERT INTO purchase_order_events (order_id, kind, actor_name, actor_kind, detail_json, created_at) "
                "VALUES ($1, 'completed', $2, 'staff', $3::jsonb, $4)",
                order_id, "导入", json.dumps({"source": "worktool2"}, ensure_ascii=False), submitted,
            )
        if status == "cancelled":
            await self._execute(
                "INSERT INTO purchase_order_events (order_id, kind, actor_name, actor_kind, detail_json, created_at) "
                "VALUES ($1, 'cancelled', $2, 'staff', $3::jsonb, $4)",
                order_id, "导入", json.dumps({"source": "worktool2"}, ensure_ascii=False), submitted,
            )
        stat.created += 1
        self._order_items += len(items)
        self._order_quantity += total_quantity

    # ---- 盘点 -----------------------------------------------------

    async def import_stock(self, docs: List[Dict[str, Any]]) -> None:
        stat = self.report.stat("material_stock")
        stat.source = len(docs)
        for index, doc in enumerate(docs):
            try:
                await self._import_stock_row(doc, index, stat)
            except Exception as exc:  # noqa: BLE001
                stat.failed += 1
                self.report.fail("material_stock 第 %s 条失败：%s" % (index + 1, exc))

    async def _import_stock_row(self, doc: Dict[str, Any], index: int, stat: Stats) -> None:
        cloud_product_id = as_text(doc.get("productId") or doc.get("product_id"))
        product = self.product_by_cloud.get(cloud_product_id) if cloud_product_id else None
        day = as_day(doc.get("date"))
        stock = as_number(doc.get("currentStock"))
        if product is None or day is None or stock is None:
            stat.skipped += 1
            if product is None:
                self._unknown_stock_products += 1
            self.report.warn("material_stock 第 %s 条缺商品/日期/库存，已跳过" % (index + 1))
            return
        counted_at = as_datetime(doc.get("countTime") or doc.get("updateTime") or doc.get("createTime"))
        if counted_at is None:
            counted_at = dt.datetime.combine(day, dt.time(12, 0), tzinfo=CHINA_TZ)
        if not self.apply:
            stat.created += 1
            return
        await self._execute(
            "INSERT INTO purchase_stock_counts (tenant_id, product_id, count_date, current_stock, "
            "counted_by_name, counted_kind, counted_at) VALUES ($1,$2,$3,$4,$5,'staff',$6) "
            "ON CONFLICT (tenant_id, product_id, count_date) DO UPDATE SET "
            "current_stock = EXCLUDED.current_stock, counted_by_name = EXCLUDED.counted_by_name, "
            "counted_kind = EXCLUDED.counted_kind, counted_at = EXCLUDED.counted_at",
            TENANT_ID, int(product["id"]), day, float(stock),
            as_text(doc.get("countByName") or doc.get("countBy"), "导入"), counted_at,
        )
        stat.created += 1

    # ---- 收尾 -----------------------------------------------------

    async def refresh_counters(self) -> None:
        if not self.apply:
            return
        suppliers = await self._fetch(
            "SELECT id, next_category_code FROM purchase_suppliers WHERE tenant_id = $1", TENANT_ID
        )
        for supplier in suppliers:
            rows = await self._fetch(
                "SELECT code FROM purchase_categories WHERE supplier_id = $1 AND code ~ '^[0-9]{2}$'",
                int(supplier["id"]),
            )
            used = [int(row["code"]) for row in rows]
            target = max(int(supplier["next_category_code"] or 1), (max(used) + 1) if used else 1)
            await self._execute(
                "UPDATE purchase_suppliers SET next_category_code = $1 WHERE id = $2",
                target, int(supplier["id"]),
            )
        categories = await self._fetch(
            "SELECT c.id, c.next_product_seq FROM purchase_categories c "
            "JOIN purchase_suppliers s ON s.id = c.supplier_id WHERE s.tenant_id = $1",
            TENANT_ID,
        )
        for category in categories:
            rows = await self._fetch(
                "SELECT product_code FROM purchase_products WHERE category_id = $1 AND product_code ~ '^[0-9]{7}$'",
                int(category["id"]),
            )
            used = [int(str(row["product_code"])[-3:]) for row in rows]
            target = max(int(category["next_product_seq"] or 1), (max(used) + 1) if used else 1)
            await self._execute(
                "UPDATE purchase_categories SET next_product_seq = $1 WHERE id = $2",
                target, int(category["id"]),
            )

    def finalize_order_report(self) -> None:
        self.report.orders = {
            "items_imported": self._order_items,
            "quantity_imported": round(self._order_quantity, 2),
            "multi_position_orders": self._multi_position_orders,
            "unknown_item_products": self._unknown_item_products,
            "unknown_stock_products": self._unknown_stock_products,
        }


async def import_source(
    source: Path,
    dsn: str,
    *,
    apply: bool,
    sync_catalog: bool = False,
    skip_orders: bool = False,
    skip_stock: bool = False,
    limit: int = 0,
    on_stage: Optional[Callable[[str], None]] = None,
) -> Report:
    """读取 8 个集合并按依赖顺序导入，返回完整报告。

    这是网页与命令行共用的入口。on_stage 只用于网页进度，抛错会被忽略，
    不允许影响导入本身。
    """
    source_dir = Path(source).expanduser().resolve()
    if not source_dir.exists():
        raise ImportSourceError("源目录不存在：%s" % source_dir)
    files = discover_files(source_dir)
    if not files:
        raise ImportSourceError(
            "%s 里没找到采购集合 JSON（suppliers/products/orders/...）" % source_dir
        )
    report = Report(str(source_dir), bool(apply))
    docs: Dict[str, List[Dict[str, Any]]] = {}
    for collection in COLLECTIONS:
        path = files.get(collection)
        if path is None:
            report.collections[collection] = {"file": None, "documents": 0, "missing": True}
            continue
        try:
            rows = load_documents(path)
            if limit:
                rows = rows[:limit]
            docs[collection] = rows
            report.collections[collection] = {"file": str(path), "documents": len(rows)}
        except Exception as exc:  # noqa: BLE001
            report.collections[collection] = {"file": str(path), "documents": 0, "error": str(exc)}
            report.fail("%s 读不出来：%s" % (collection, exc))
    if not docs:
        raise ImportSourceError("没有可读的集合文件")

    def stage(name: str) -> None:
        if on_stage is None:
            return
        try:
            on_stage(name)
        except Exception:  # noqa: BLE001 - 进度回调不能影响导入
            pass

    stage("connecting")
    conn = await asyncpg.connect(dsn)
    try:
        runner = ImportRunner(
            conn, report, apply=bool(apply), sync_catalog=bool(sync_catalog)
        )
        stage("checking_schema")
        await runner.check_schema()
        if docs.get("positions"):
            stage("positions")
            await runner.import_positions(docs["positions"])
        if docs.get("suppliers"):
            stage("suppliers")
            await runner.import_suppliers(docs["suppliers"])
        if docs.get("categories"):
            stage("categories")
            await runner.import_categories(docs["categories"])
        if docs.get("products"):
            stage("products")
            await runner.import_products(docs["products"])
        if docs.get("date_factor_tags"):
            stage("date_factor_tags")
            await runner.import_date_tags(docs["date_factor_tags"])
        if docs.get("date_factor_weekday_defaults"):
            stage("date_factor_weekday_defaults")
            await runner.import_weekday_defaults(docs["date_factor_weekday_defaults"])
        if docs.get("orders") and not skip_orders:
            stage("orders")
            await runner.import_orders(docs["orders"])
        if docs.get("material_stock") and not skip_stock:
            stage("material_stock")
            await runner.import_stock(docs["material_stock"])
        stage("refresh_counters")
        await runner.refresh_counters()
        runner.finalize_order_report()
    finally:
        await conn.close()
    stage("done")
    return report


async def run(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    print("源目录：%s" % source)
    print("目标库：%s" % redact_dsn(args.dsn))
    print("模式：%s" % ("APPLY（会写库）" if args.apply else "DRY-RUN（只读）"))
    try:
        report = await import_source(
            source,
            args.dsn,
            apply=bool(args.apply),
            sync_catalog=bool(args.sync_catalog),
            skip_orders=bool(args.skip_orders),
            skip_stock=bool(args.skip_stock),
            limit=int(args.limit or 0),
        )
    except ImportSourceError as exc:
        print("✗ %s" % exc)
        return 2
    payload = report.to_dict()
    if args.report:
        target = Path(args.report).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("报告已写入：%s" % target)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if not report.failures else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="worktool2 采购数据导入 luyun")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="8 个集合 JSON 所在目录")
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="目标 PostgreSQL DSN（默认读 POSTGRES_DSN）")
    parser.add_argument("--apply", action="store_true", help="实际写库；不加就是 dry-run")
    parser.add_argument("--sync-catalog", action="store_true", help="已存在的商品也按源数据更新（默认只补缺）")
    parser.add_argument("--skip-orders", action="store_true", help="不导入订单")
    parser.add_argument("--skip-stock", action="store_true", help="不导入盘点")
    parser.add_argument("--limit", type=int, default=0, help="每个集合只取前 N 条（演练用）")
    parser.add_argument("--report", default="", help="把 JSON 报告写到这个路径")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print("✗ 导入失败：%s" % exc, file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
