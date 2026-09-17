#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite SQL → PostgreSQL SQL 的运行时方言转换。

现有 db_core 下的 SQL 全部按 SQLite 方言书写（413 个 execute 调用点）。与其把
每个 repo 重写一遍，不如在驱动边界做一次转换：方言差异经全仓统计只有下列 9
类，且大多是机械替换。

| SQLite                          | PostgreSQL                          | 全仓处数 |
|---------------------------------|-------------------------------------|----------|
| `?` 占位符                       | `$1, $2, ...`                       | 87 行    |
| `IFNULL(a, b)`                  | `COALESCE(a, b)`                    | 9        |
| `INSERT OR IGNORE INTO`         | `INSERT INTO` + `ON CONFLICT NOTHING` | 9      |
| `strftime('%H', x)`             | `EXTRACT(HOUR FROM x::timestamptz)` | 1        |
| `strftime('%Y-%m', x)`          | `to_char(x::timestamptz, 'YYYY-MM')` | 1       |
| `strftime('%Y-W%W', x)`         | `to_char(x::timestamptz, 'IYYY"W"IW')` | 1 ⚠️   |
| `x COLLATE NOCASE`              | `lower(x)`                          | 2        |
| `rowid`                         | `id`                                | 56       |
| `cursor.lastrowid`              | `INSERT ... RETURNING id`           | 24       |

`SUBSTR(a, b, c)` 两边语法一致，无需转换。

⚠️ `%W`（SQLite 周，周一为一周起点，00–53）与 `IW`（ISO 8601 周）在**跨年
边界**语义不同。当前仅 `db_core/reports.py` 的周维度报表使用，迁移后需要用
真实数据对拍，不能默认等价。

新增 SQL 若用到上面的表之外的新方言特性，必须在此登记并补测试——否则 PG
后端会静默给出错误结果，而不是报错。
"""

from __future__ import annotations

import re

__all__ = ["translate", "DIALECT_RULES"]

_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_IFNULL = re.compile(r"\bIFNULL\s*\(", re.IGNORECASE)
_STRFTIME_HOUR = re.compile(
    r"strftime\(\s*'%H'\s*,\s*([^)]+?)\s*\)", re.IGNORECASE
)
_STRFTIME_MONTH = re.compile(
    r"strftime\(\s*'%Y-%m'\s*,\s*([^)]+?)\s*\)", re.IGNORECASE
)
_STRFTIME_WEEK = re.compile(
    r"strftime\(\s*'%Y-W%W'\s*,\s*([^)]+?)\s*\)", re.IGNORECASE
)
_COLLATE_NOCASE = re.compile(
    r"([\w.\"]+)\s+COLLATE\s+NOCASE", re.IGNORECASE
)
_ROWID = re.compile(r"\browid\b", re.IGNORECASE)
# 一次扫描同时处理两种形态：`rowid AS rowid`（保留别名，admin 靠它定位行）优先于
# 裸 `rowid`。分开两次替换会让刚生成的 `AS rowid` 被第二次替换再改掉。
_ROWID_ANY = re.compile(r"\browid\s+AS\s+rowid\b|\browid\b", re.IGNORECASE)

DIALECT_RULES = (
    "? → $n（跳过字符串字面量）",
    "IFNULL → COALESCE",
    "INSERT OR IGNORE → ON CONFLICT DO NOTHING",
    "strftime('%H'|'%Y-%m'|'%Y-W%W') → EXTRACT / to_char",
    "COLLATE NOCASE → lower()",
    "rowid → 该表的行标识列（默认 id；rowid AS rowid 保留别名）",
)


def _rewrite_insert_or_ignore(sql: str) -> str:
    if not _INSERT_OR_IGNORE.search(sql):
        return sql
    sql = _INSERT_OR_IGNORE.sub("INSERT INTO", sql)
    if "ON CONFLICT" in sql.upper():
        return sql
    return sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"


def _rewrite_functions(sql: str) -> str:
    sql = _IFNULL.sub("COALESCE(", sql)
    # 非 raw 字符串：正则反向引用要写 \1，而 PG 格式串里的双引号不能带反斜杠
    sql = _STRFTIME_WEEK.sub(
        "to_char((\\1)::timestamptz, 'IYYY\"W\"IW')", sql
    )
    sql = _STRFTIME_MONTH.sub(r"to_char((\1)::timestamptz, 'YYYY-MM')", sql)
    sql = _STRFTIME_HOUR.sub(
        r"EXTRACT(HOUR FROM (\1)::timestamptz)::int", sql
    )
    return sql


def _rewrite_collate(sql: str) -> str:
    # SQLite 的 NOCASE 是「排序时不区分大小写」。PG 无等价 collation 名，
    # lower() 能在 ORDER BY / 比较两种位置上给出同样的排序语义。
    return _COLLATE_NOCASE.sub(r"lower(\1)", sql)


def _rewrite_rowid(sql: str, column: str = "id") -> str:
    # SQLite 的隐式 rowid 对**任何**表都存在；PG 只有显式列。所以 rowid 要映射到
    # 「该表的行标识列」——有 id 列就是 id，否则是主键第一列（sessions→session_id、
    # api_tokens→token_hash、sop_stations→slug...）。列名由调用方查 schema 提供。
    def replace(match: "re.Match[str]") -> str:
        if " as " in match.group(0).lower():
            return f"{column} AS rowid"
        return column

    return _ROWID_ANY.sub(replace, sql)


def _rewrite_placeholders(sql: str) -> str:
    """`?` → `$1..$n`，跳过单引号/双引号字面量内的问号。"""
    out = []
    index = 0
    in_single = False
    in_double = False
    length = len(sql)
    i = 0
    while i < length:
        ch = sql[i]
        if ch == "'" and not in_double:
            # SQL 里 '' 是转义的单引号，不能当作字面量边界
            if in_single and i + 1 < length and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "?" and not in_single and not in_double:
            index += 1
            out.append(f"${index}")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_TABLE_INFO = re.compile(
    r"PRAGMA\s+(?:\w+\.)?table_info\(\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*\)",
    re.IGNORECASE,
)
# PRAGMA table_info 的 PG 等价物。列序必须与 SQLite 一致（cid, name, type,
# notnull, dflt_value, pk）——调用方按位置取值（r[1] 名称 / r[2] 类型 / r[5] 主键）。
_TABLE_INFO_PG = (
    "SELECT (c.ordinal_position - 1)::int AS cid,"
    " c.column_name AS name,"
    " c.data_type AS type,"
    " CASE WHEN c.is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,"
    " c.column_default AS dflt_value,"
    " CASE WHEN pk.attname IS NOT NULL THEN 1 ELSE 0 END AS pk"
    " FROM information_schema.columns c"
    " LEFT JOIN (SELECT a.attname, t.relname FROM pg_index i"
    "   JOIN pg_class t ON t.oid = i.indrelid"
    "   JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY (i.indkey)"
    "   WHERE i.indisprimary) pk"
    "  ON pk.attname = c.column_name AND pk.relname = c.table_name"
    " WHERE c.table_name = '{table}'"
    " AND c.table_schema = ANY (current_schemas(false))"
    " ORDER BY c.ordinal_position"
)


def _rewrite_pragma(sql: str) -> str:
    """把 ``PRAGMA table_info(x)`` 换成 information_schema 查询。

    其余 PRAGMA（journal_mode / busy_timeout / optimize / quick_check…）都是
    SQLite 连接级或维护语句，PG 分支本就不会走到，不在此处理。
    """
    return _TABLE_INFO.sub(lambda m: _TABLE_INFO_PG.format(table=m.group(1)), sql)


def translate(sql: str, rowid_column: str = "id") -> str:
    """把一条 SQLite 方言的 SQL 转成 PostgreSQL 方言。

    ``rowid_column`` 是该 SQL 涉及表的行标识列，由调用方查 schema 后传入
    （见 :meth:`db_core.backend.pg.PgConnection.row_key_column`）。
    """
    sql = _rewrite_pragma(sql)
    sql = _rewrite_insert_or_ignore(sql)
    sql = _rewrite_functions(sql)
    sql = _rewrite_collate(sql)
    sql = _rewrite_rowid(sql, rowid_column)
    return _rewrite_placeholders(sql)
