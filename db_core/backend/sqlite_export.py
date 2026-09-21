#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把任意后端连接里的表导出成 SQLite 库。

SQLite 后端的整库导出走 aiosqlite 的 ``backup``（页级拷贝，数据不进 Python 进程）；
PostgreSQL 没有等价物，只能按表重建。本模块集中放「读列定义 → 建表 → 搬数据」这段
后端无关的逻辑给 :meth:`db_core.backend.pg.PgConnection.backup` 用；其中的类型与取值
折算函数与备份服务的配方导出（``services.backup_service.export_recipes_db_bytes_from_conn``）
共用，避免同一个折算在两处各写一份、日后分叉。

**为什么分批取数**：``PgConnection.execute`` 走 asyncpg 的 ``fetch``，一次把整个结果
集取回内存。orders 在门店库里有近 20 万行，整表 SELECT 会让导出期间的内存峰值失控。
``LIMIT/OFFSET`` 分页实测与一次性 fetch 速度相当（本地库 18.5 万行：0.31s vs 0.21s），
所以按 :data:`EXPORT_BATCH_ROWS` 分页，内存峰值固定在每批的行数上。

**分页必须有序**：窗口按行标识列排（见 :func:`_stable_order_key`）。没有 ``ORDER BY``
时 PG 按物理顺序切窗口，而被 UPDATE 的行会换物理位置，窗口随之漂移、同一行被取第二次
——导出的 sqlite 会撞主键冲突（实测 orders 表第 10 批与前面重复 1039 行）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Sequence

# 每批搬运的行数：够大以免分页往返太多，够小以免内存峰值失控。
EXPORT_BATCH_ROWS = 5000


def sqlite_column_type(sql_type: str) -> str:
    """把源库列类型折算成 SQLite 存储类。

    导出件只要求「可读 + 可回灌」，不追求还原源库的精确类型：两种恢复路径都按
    列名做交集（``merge_recipes_from_bytes`` / ``overwrite_recipes_from_bytes``），
    约束由目标库自己的表定义负责。
    """
    normalized = (sql_type or "").lower()
    if "int" in normalized:
        return "INTEGER"
    if any(
        token in normalized
        for token in ("real", "double", "numeric", "decimal", "float")
    ):
        return "REAL"
    if any(token in normalized for token in ("blob", "bytea")):
        return "BLOB"
    return "TEXT"


def sqlite_bindable(value: Any) -> Any:
    """把源库取回的值收敛成 ``sqlite3`` 能绑定的类型。

    业务表里的时间戳是 TEXT、金额是 REAL/DOUBLE，但加成性迁移可能引入
    ``timestamptz`` / ``numeric`` / ``boolean``——asyncpg 会给出 datetime /
    Decimal / bool 对象，直接交给 sqlite3 会 ``InterfaceError``。
    """
    if isinstance(value, bool):
        # 必须在 int 分支之前判：bool 是 int 的子类，否则会被当整数原样返回
        # （sqlite3 恰好也接受 bool，但显式折算让「PG 的 bool 列」有确定的落库形态）。
        return int(value)
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return str(value)


def quote_ident(name: str) -> str:
    """SQLite 标识符加引号——列名里有保留字（orders 的 ``status``、``source``）。"""
    return '"' + str(name).replace('"', '""') + '"'


async def _table_columns(conn, table: str) -> List[tuple]:
    """读列定义，返回 ``[(列名, 类型, 主键标志)]``；源库没有该表时返回空。

    表名不加引号：``PRAGMA table_info`` 的方言转换靠正则匹配裸标识符，加引号会让
    PG 侧匹配不上、退回成 PG 不认识的 PRAGMA。
    """
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return [
        (str(row[1]), str(row[2] or ""), int(row[5] or 0))
        for row in rows
    ]


def _create_table_sql(table: str, columns: Sequence[tuple]) -> str:
    primary = [name for name, _type, pk in columns if pk]
    definitions = []
    for name, sql_type, pk in columns:
        definition = f"{quote_ident(name)} {sqlite_column_type(sql_type)}"
        # 只还原单列主键：导入侧靠业务唯一键（TABLE_DEDUP_KEY）去重，复合主键声明
        # 写错反而会让整个成员不可回灌。
        if pk and len(primary) == 1:
            definition += " PRIMARY KEY"
        definitions.append(definition)
    return f"CREATE TABLE {quote_ident(table)} ({', '.join(definitions)})"


async def _stable_order_key(conn, table: str, columns: Sequence[tuple]) -> str:
    """分页用的排序列。

    **分页必须按确定的顺序取**：``LIMIT/OFFSET`` 在没有 ``ORDER BY`` 时按物理顺序
    切窗口，而被 UPDATE 的行会换物理位置（HOT 更新 / VACUUM），窗口跟着漂移——同一行
    会被取第二次，导出的 sqlite 撞上主键冲突（实测 orders：第 10 批与前面批重复 1039
    行）。所以按行标识列排序，窗口才稳定。

    PG 连接自己知道行标识列（``row_key_column``：有 id 用 id，否则主键第一列）；
    其他连接退回主键第一列、再退回第一列。
    """
    resolver = getattr(conn, "row_key_column", None)
    if resolver is not None:
        return await resolver(table)
    primary = [name for name, _type, pk in columns if pk]
    return primary[0] if primary else columns[0][0]


async def _copy_rows(conn, dst, table: str, names: Sequence[str], key: str) -> None:
    """把一张表的全部行分批搬进 ``dst``（按 ``key`` 有序，见 :func:`_stable_order_key`）。"""
    columns = ", ".join(quote_ident(name) for name in names)
    placeholders = ", ".join(["?"] * len(names))
    insert_sql = f"INSERT INTO {quote_ident(table)} ({columns}) VALUES ({placeholders})"
    select_sql = (
        f"SELECT {columns} FROM {quote_ident(table)}"
        f" ORDER BY {quote_ident(key)} LIMIT ? OFFSET ?"
    )

    offset = 0
    while True:
        cursor = await conn.execute(select_sql, (EXPORT_BATCH_ROWS, offset))
        rows = await cursor.fetchall()
        if not rows:
            return
        await dst.executemany(
            insert_sql,
            [tuple(sqlite_bindable(value) for value in row) for row in rows],
        )
        if len(rows) < EXPORT_BATCH_ROWS:
            return
        offset += len(rows)


async def export_tables_to_sqlite(conn, tables: Sequence[str], dst) -> List[str]:
    """把 ``conn`` 上 ``tables`` 的表结构与数据写进 ``dst``（**空的** sqlite 库）。

    ``dst`` 是 aiosqlite 连接。源库里不存在的表会被跳过而不是造一张空表——否则
    导入侧会看到一堆源库根本没有的表。返回实际导出的表名。

    各表之间**不是**同一个事务快照：每批之间连接锁都会释放（否则导出会把爬虫的写入
    挡住几十秒）。所以导出期间的删除可能让某几行不进导出件——这是导出件与「页级
    backup」的差别，回灌侧本来就按业务唯一键做合并，不会因此写坏数据。
    """
    exported: List[str] = []
    for table in tables:
        columns = await _table_columns(conn, table)
        if not columns:
            continue
        await dst.execute(_create_table_sql(table, columns))
        names = [name for name, _type, _pk in columns]
        key = await _stable_order_key(conn, table, columns)
        await _copy_rows(conn, dst, table, names, key)
        exported.append(table)
    await dst.commit()
    return exported
