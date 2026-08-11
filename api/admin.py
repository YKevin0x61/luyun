#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库管理 API 路由
支持增删改查、字段管理
"""

import logging
import os
import re
import asyncio
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Depends, UploadFile, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from config import settings
from database import ALL_TABLES, get_db
from api.security import verify_admin_token
from services.dish_catalog import get_dish_catalog

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("admin_audit")

CHINA_TZ = timezone(timedelta(hours=8))

# Generic Admin DataTable may read these; writes must go through DishCatalog.
_READ_ONLY_ADMIN_TABLES = frozenset({"dish_stations"})

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_token)]
)


def _reject_read_only_table_write(table_name: str) -> None:
    if table_name in _READ_ONLY_ADMIN_TABLES:
        raise HTTPException(
            status_code=403,
            detail="dish_stations 为只读表，请通过 /api/dish-stations 维护映射",
        )


# ==================== Pydantic 模型 ====================

class ColumnAdd(BaseModel):
    column_name: str
    column_type: str
    default_value: Optional[str] = None
    nullable: bool = True


class RowCreate(BaseModel):
    values: Dict[str, Any]


class RowUpdate(BaseModel):
    values: Dict[str, Any]


class RowBatchDelete(BaseModel):
    row_ids: List[int]


class RowBatchUpdate(BaseModel):
    row_ids: List[int]
    column: str
    value: Any

BATCH_DELETE_MAX = 500
BATCH_UPDATE_MAX = 500

_BATCH_FORBIDDEN_COLUMNS = frozenset({"rowid", "id", "created_at"})


class ReconcileRequest(BaseModel):
    date: Optional[str] = None
    fix: bool = False
    notify: bool = True


# ─── 辅助：获取某表的共享连接 ───

def _table_conn(db, table_name: str):
    """经 DatabaseManager.table() 取得连接，避免调用方直碰内部字典。"""
    try:
        return db.table(table_name).conn
    except KeyError:
        raise HTTPException(status_code=400, detail=f"未知表: {table_name}")


async def _load_table_columns(conn, table_name: str) -> List[Dict[str, Any]]:
    async with conn.cursor() as cursor:
        await cursor.execute(f"PRAGMA table_info({table_name})")
        rows = await cursor.fetchall()
    return [{"name": r[1], "type": r[2], "pk": bool(r[5])} for r in rows]


def _validate_batch_update_column(column: str, table_columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', column):
        raise HTTPException(status_code=400, detail="无效的字段名")
    col_lower = column.lower()
    if col_lower in _BATCH_FORBIDDEN_COLUMNS:
        raise HTTPException(status_code=403, detail=f"禁止批量修改字段: {column}")

    col_meta = next((c for c in table_columns if c["name"].lower() == col_lower), None)
    if not col_meta:
        raise HTTPException(status_code=400, detail=f"字段 {column} 不存在")
    if col_meta["pk"]:
        raise HTTPException(status_code=403, detail=f"禁止批量修改主键字段: {column}")
    return col_meta


def _audit(action: str, table_name: str = "-", detail: str = ""):
    audit_logger.info(
        "action=%s table=%s detail=%s time=%s",
        action,
        table_name,
        detail,
        datetime.now(CHINA_TZ).isoformat()
    )


async def _broadcast_admin_event(action: str, table_name: str = "-", **payload):
    try:
        from main import broadcast_realtime_event
        await broadcast_realtime_event(
            "admin_data_changed",
            action=action,
            table=table_name,
            **payload
        )
    except Exception as exc:
        logger.debug(f"广播管理事件失败: {exc}")


# ==================== 路由实现 ====================

@router.get("/tables")
async def list_tables(db=Depends(get_db)):
    """列出所有数据表"""
    return {"success": True, "tables": ALL_TABLES}


@router.get("/tables/{table_name}/schema")
async def get_table_schema(table_name: str, db=Depends(get_db)):
    """获取表结构"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    if table_name not in ALL_TABLES:
        raise HTTPException(status_code=403, detail="系统表禁止访问")

    try:
        conn = _table_conn(db, table_name)
        async with conn.cursor() as cursor:
            await cursor.execute(f"PRAGMA table_info({table_name})")
            rows = await cursor.fetchall()
        columns = [{
            "cid": r[0], "name": r[1], "type": r[2],
            "notnull": bool(r[3]), "dflt_value": r[4], "pk": bool(r[5])
        } for r in rows]
        return {"success": True, "table": table_name, "columns": columns}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取表结构失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}/rows")
async def get_table_rows(
    table_name: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search_field: Optional[str] = None,
    search_value: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_dir: str = Query("asc", regex="^(asc|desc)$"),
    db=Depends(get_db),
):
    """分页获取表数据"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    if table_name not in ALL_TABLES:
        raise HTTPException(status_code=403, detail="系统表禁止访问")

    try:
        conn = _table_conn(db, table_name)
        async with conn.cursor() as cursor:
            conditions, params = [], []
            if search_field and search_value:
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', search_field):
                    raise HTTPException(status_code=400, detail="无效的搜索字段")
                conditions.append(f"{search_field} LIKE ?")
                params.append(f"%{search_value}%")

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            order = ""
            if sort_field:
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', sort_field):
                    raise HTTPException(status_code=400, detail="无效的排序字段")
                order = f"ORDER BY {sort_field} {sort_dir.upper()}"

            await cursor.execute(f"SELECT COUNT(*) FROM {table_name} {where}", params)
            total = (await cursor.fetchone())[0]

            offset = (page - 1) * page_size
            await cursor.execute(
                f"SELECT rowid AS rowid, * FROM {table_name} {where} {order} LIMIT ? OFFSET ?",
                params + [page_size, offset]
            )
            rows = await cursor.fetchall()
            columns = [d[0] for d in cursor.description] if cursor.description else []

        results = []
        for row in rows:
            d = {}
            for i, col in enumerate(columns):
                val = row[i]
                if isinstance(val, str) and val:
                    try:
                        val = datetime.fromisoformat(val)
                    except Exception:
                        pass
                d[col] = val
            results.append(d)

        return {
            "success": True, "table": table_name, "rows": results,
            "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total > 0 else 1,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取表数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tables/{table_name}/rows")
async def insert_row(table_name: str, row: RowCreate, db=Depends(get_db)):
    """插入一行数据"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    _reject_read_only_table_write(table_name)

    try:
        user_cols = [c for c in row.values.keys()
                     if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', c)]
        all_cols = list(set(user_cols + ["created_at", "updated_at"]))
        if not all_cols:
            raise HTTPException(status_code=400, detail="没有有效字段可插入")

        now = datetime.now(CHINA_TZ).isoformat()
        values = []
        for c in all_cols:
            val = row.values.get(c)
            if val == "" or val is None:
                val = now
            values.append(val)

        conn = _table_conn(db, table_name)
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"INSERT INTO {table_name} ({', '.join(all_cols)}) VALUES ({', '.join(['?'] * len(all_cols))})",
                values
            )
        await db.table(table_name).commit()

        async with conn.cursor() as cursor:
            await cursor.execute(f"SELECT rowid FROM {table_name} ORDER BY rowid DESC LIMIT 1")
            r = await cursor.fetchone()
        new_id = r[0] if r else None

        logger.info(f"✅ 插入 {table_name} 行成功: {new_id}")
        _audit("insert_row", table_name, f"rowid={new_id}, columns={','.join(all_cols)}")
        await _broadcast_admin_event("insert_row", table_name, rowid=new_id)
        return {"success": True, "rowid": new_id, "message": f"插入成功 (ID: {new_id})"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"插入行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/tables/{table_name}/rows/{row_id}")
async def update_row(table_name: str, row_id: int, row: RowUpdate, db=Depends(get_db)):
    """更新一行数据"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    _reject_read_only_table_write(table_name)

    try:
        cols = [c for c in row.values.keys()
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', c) and c.lower() not in ("rowid", "id")]
        if not cols:
            raise HTTPException(status_code=400, detail="没有有效字段可更新")

        now = datetime.now(CHINA_TZ).isoformat()
        for c in cols:
            if c.lower() == "updated_at" and (row.values[c] == "" or row.values[c] is None):
                row.values[c] = now

        set_clause = ", ".join([f"{c} = ?" for c in cols])
        values = [row.values[c] for c in cols] + [row_id]
        logger.warning(f"[UPDATE] {table_name} rowid={row_id} cols={cols} values={[row.values[c] for c in cols]}")

        conn = _table_conn(db, table_name)
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE {table_name} SET {set_clause} WHERE rowid = ?",
                values
            )
            affected = cursor.rowcount
            await conn.commit()

        logger.info(f"✅ 更新 {table_name} rowid={row_id}, 影响行数: {affected}")
        _audit("update_row", table_name, f"rowid={row_id}, affected={affected}, columns={','.join(cols)}")
        await _broadcast_admin_event("update_row", table_name, rowid=row_id, affected=affected)
        return {"success": True, "affected": affected, "message": f"更新成功 (影响: {affected} 行)"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/tables/{table_name}/rows/{row_id}")
async def delete_row(table_name: str, row_id: int, db=Depends(get_db)):
    """删除一行数据"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    if table_name not in ALL_TABLES:
        raise HTTPException(status_code=403, detail="系统表禁止修改")
    _reject_read_only_table_write(table_name)

    try:
        conn = _table_conn(db, table_name)
        async with conn.cursor() as cursor:
            await cursor.execute(f"DELETE FROM {table_name} WHERE rowid = ?", (row_id,))
        await db.table(table_name).commit()
        affected = cursor.rowcount

        logger.info(f"✅ 删除 {table_name} rowid={row_id}, 影响行数: {affected}")
        _audit("delete_row", table_name, f"rowid={row_id}, affected={affected}")
        await _broadcast_admin_event("delete_row", table_name, rowid=row_id, affected=affected)
        return {"success": True, "affected": affected, "message": f"删除成功 (影响: {affected} 行)"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tables/{table_name}/rows/batch-delete")
async def batch_delete_rows(table_name: str, body: RowBatchDelete, db=Depends(get_db)):
    """批量删除多行数据（按 rowid）。"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    if table_name not in ALL_TABLES:
        raise HTTPException(status_code=403, detail="系统表禁止修改")
    _reject_read_only_table_write(table_name)

    row_ids = list(dict.fromkeys(body.row_ids))
    if not row_ids:
        raise HTTPException(status_code=400, detail="row_ids 不能为空")
    if len(row_ids) > BATCH_DELETE_MAX:
        raise HTTPException(status_code=400, detail=f"单次最多删除 {BATCH_DELETE_MAX} 条")
    if any(rid <= 0 for rid in row_ids):
        raise HTTPException(status_code=400, detail="row_id 必须为正整数")

    try:
        conn = _table_conn(db, table_name)
        placeholders = ",".join("?" for _ in row_ids)
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"DELETE FROM {table_name} WHERE rowid IN ({placeholders})",
                row_ids,
            )
            affected = cursor.rowcount
        await db.table(table_name).commit()

        logger.info(f"✅ 批量删除 {table_name} {len(row_ids)} 个 rowid, 影响行数: {affected}")
        _audit(
            "batch_delete_rows",
            table_name,
            f"requested={len(row_ids)}, affected={affected}, rowids={row_ids[:20]}",
        )
        await _broadcast_admin_event(
            "batch_delete_rows",
            table_name,
            affected=affected,
            count=len(row_ids),
        )
        return {
            "success": True,
            "affected": affected,
            "message": f"批量删除成功 (影响: {affected} 行)",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量删除行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tables/{table_name}/rows/batch-update")
async def batch_update_rows(table_name: str, body: RowBatchUpdate, db=Depends(get_db)):
    """批量将选中行的同一字段更新为同一值。"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    if table_name not in ALL_TABLES:
        raise HTTPException(status_code=403, detail="系统表禁止修改")
    _reject_read_only_table_write(table_name)

    row_ids = list(dict.fromkeys(body.row_ids))
    if not row_ids:
        raise HTTPException(status_code=400, detail="row_ids 不能为空")
    if len(row_ids) > BATCH_UPDATE_MAX:
        raise HTTPException(status_code=400, detail=f"单次最多更新 {BATCH_UPDATE_MAX} 条")
    if any(rid <= 0 for rid in row_ids):
        raise HTTPException(status_code=400, detail="row_id 必须为正整数")

    try:
        conn = _table_conn(db, table_name)
        table_columns = await _load_table_columns(conn, table_name)
        col_meta = _validate_batch_update_column(body.column, table_columns)
        column = col_meta["name"]

        now = datetime.now(CHINA_TZ).isoformat()
        set_parts = [f"{column} = ?"]
        params: List[Any] = [body.value]
        has_updated_at = any(c["name"].lower() == "updated_at" for c in table_columns)
        if has_updated_at and column.lower() != "updated_at":
            set_parts.append("updated_at = ?")
            params.append(now)

        placeholders = ",".join("?" for _ in row_ids)
        params.extend(row_ids)
        sql = f"UPDATE {table_name} SET {', '.join(set_parts)} WHERE rowid IN ({placeholders})"

        async with conn.cursor() as cursor:
            await cursor.execute(sql, params)
            affected = cursor.rowcount
        await db.table(table_name).commit()

        logger.info(
            f"✅ 批量更新 {table_name}.{column} rowids={len(row_ids)}, 影响行数: {affected}"
        )
        _audit(
            "batch_update_rows",
            table_name,
            f"column={column}, requested={len(row_ids)}, affected={affected}, rowids={row_ids[:20]}",
        )
        await _broadcast_admin_event(
            "batch_update_rows",
            table_name,
            column=column,
            affected=affected,
            count=len(row_ids),
        )
        return {
            "success": True,
            "affected": affected,
            "message": f"批量更新成功 (影响: {affected} 行)",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量更新行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tables/{table_name}/columns")
async def add_column(table_name: str, col: ColumnAdd, db=Depends(get_db)):
    """添加字段"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    _reject_read_only_table_write(table_name)
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col.column_name):
        raise HTTPException(status_code=400, detail="无效的字段名")
    col_type = col.column_type.upper()
    if col_type not in ("TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC"):
        raise HTTPException(status_code=400, detail="不支持的字段类型")

    try:
        sql = f"ALTER TABLE {table_name} ADD COLUMN {col.column_name} {col_type}"
        if not col.nullable:
            sql += " NOT NULL"
        if col.default_value is not None:
            sql += f" DEFAULT {'?' if col_type in ('TEXT',) else col.default_value}"

        conn = _table_conn(db, table_name)
        async with conn.cursor() as cursor:
            await cursor.execute(sql)
        await db.table(table_name).commit()

        logger.info(f"✅ 添加字段 {table_name}.{col.column_name} ({col_type}) 成功")
        _audit("add_column", table_name, f"column={col.column_name}, type={col_type}")
        await _broadcast_admin_event("add_column", table_name, column=col.column_name)
        return {"success": True, "message": f"字段 {col.column_name} ({col_type}) 添加成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"添加字段失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/tables/{table_name}/columns/{column_name}")
async def drop_column(table_name: str, column_name: str, db=Depends(get_db)):
    """删除字段（重建表）"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")
    _reject_read_only_table_write(table_name)
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', column_name):
        raise HTTPException(status_code=400, detail="无效的字段名")
    forbidden = {"id", "rowid", "created_at", "updated_at"}
    if column_name.lower() in forbidden:
        raise HTTPException(status_code=403, detail=f"禁止删除字段: {column_name}")

    try:
        conn = _table_conn(db, table_name)
        async with conn.cursor() as cursor:
            await cursor.execute(f"PRAGMA table_info({table_name})")
            old_cols = [r[1] for r in await cursor.fetchall()]

        if column_name not in old_cols:
            raise HTTPException(status_code=404, detail=f"字段 {column_name} 不存在")

        new_cols = [c for c in old_cols if c != column_name]
        tmp_table = f"{table_name}_tmp_{datetime.now().strftime('%H%M%S')}"

        async with conn.cursor() as cursor:
            await cursor.execute(f"CREATE TABLE {tmp_table} AS SELECT {', '.join(new_cols)} FROM {table_name}")
            await cursor.execute(f"DROP TABLE {table_name}")
            await cursor.execute(f"ALTER TABLE {tmp_table} RENAME TO {table_name}")
        await db.table(table_name).commit()

        logger.info(f"✅ 删除字段 {table_name}.{column_name} 成功")
        _audit("drop_column", table_name, f"column={column_name}")
        await _broadcast_admin_event("drop_column", table_name, column=column_name)
        return {"success": True, "message": f"字段 {column_name} 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除字段失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}/stats")
async def get_table_stats(table_name: str, db=Depends(get_db)):
    """获取表统计信息"""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise HTTPException(status_code=400, detail="无效的表名")

    try:
        conn = _table_conn(db, table_name)
        async with conn.cursor() as cursor:
            await cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = (await cursor.fetchone())[0]
            await cursor.execute(f"PRAGMA table_info({table_name})")
            columns = [{"name": r[1], "type": r[2], "pk": bool(r[5])} for r in await cursor.fetchall()]
        return {"success": True, "table": table_name, "row_count": count, "columns": columns}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-stations")
async def sync_orders_stations(dish_catalog=Depends(get_dish_catalog)):
    """
    从 dish_stations 映射表同步 orders 表档口字段（今日订单）
    """
    try:
        today_start = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        return await dish_catalog.sync_orders_since(today_start)
    except Exception as e:
        logger.error(f"同步档口失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/unmapped-dishes")
async def get_unmapped_dishes(dish_catalog=Depends(get_dish_catalog)):
    """获取订单中未分类的菜品列表"""
    try:
        return await dish_catalog.unmapped_dishes()
    except Exception as e:
        logger.error(f"获取未分类菜品失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── DB 文件导入 / 导出 ───────────────────────────────────

import aiosqlite

from services.backup_service import TABLE_DEDUP_KEY, merge_app_db_from_file


def _unlink_export_temp(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@router.get("/export/db")
async def export_db(db=Depends(get_db)):
    """
    导出合并后的单个 .db 文件（含 ALL_TABLES），可与「导入 DB」互操作。
    """
    ts = datetime.now(CHINA_TZ).strftime("%Y%m%d-%H%M%S")
    filename = f"luyun-export-{ts}.db"
    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="luyun-export-")
    os.close(fd)
    try:
        await db.export_merged_sqlite_file(tmp_path)
        _audit("export_db", "merged", f"path={filename}")
        await _broadcast_admin_event("export_db", "merged")
        return FileResponse(
            tmp_path,
            filename=filename,
            media_type="application/vnd.sqlite3",
            background=BackgroundTask(_unlink_export_temp, tmp_path),
        )
    except Exception as e:
        _unlink_export_temp(tmp_path)
        logger.error(f"导出 DB 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import/preview")
async def import_db_preview(file: UploadFile, db=Depends(get_db)):
    """
    预览上传的 .db 文件内容。
    返回每个表的：源行数、目标行数、预计导入行数。
    """
    if not file.filename.endswith('.db'):
        raise HTTPException(status_code=400, detail="仅支持 .db 文件")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    # 在临时文件中打开只读连接
    fd = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    fd.write(content)
    fd.close()

    try:
        src_conn = await aiosqlite.connect(fd.name)
        src_conn.row_factory = aiosqlite.Row

        preview = []
        for table in ALL_TABLES:
            src_cur = await src_conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            )
            src_cnt = (await src_cur.fetchone())[0]

            dst_tdb = db.table_or_none(table)
            if dst_tdb is None:
                continue
            async with dst_tdb.conn.cursor() as dst_cur:
                await dst_cur.execute(f"SELECT COUNT(*) FROM {table}")
                dst_cnt = (await dst_cur.fetchone())[0]

            dedup_key = TABLE_DEDUP_KEY.get(table)
            missing = src_cnt
            if dedup_key and src_cnt > 0:
                src_keys = set()
                async with src_conn.execute(f"SELECT {dedup_key} FROM {table}") as cur:
                    rows = await cur.fetchall()
                    src_keys = {row[0] for row in rows if row[0]}

                async with dst_tdb.conn.cursor() as dst_cur:
                    await dst_cur.execute(f"SELECT {dedup_key} FROM {table}")
                    rows = await dst_cur.fetchall()
                    dst_keys = {row[0] for row in rows if row[0]}

                missing = len(src_keys - dst_keys)

            preview.append({
                "table": table,
                "src_rows": src_cnt,
                "dst_rows": dst_cnt,
                "will_import": missing,
                "dedup_key": dedup_key,
            })

        await src_conn.close()
        return {"success": True, "file_size": len(content), "preview": preview}

    except Exception as e:
        logger.error(f"预览导入失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        import os
        os.unlink(fd.name)


@router.post("/import/execute")
async def import_db_execute(
    file: UploadFile,
    tables: str = "",   # 逗号分隔的表名列表，空=全部
    db=Depends(get_db),
):
    """
    执行 DB 文件导入。
    按唯一键去重（business_flow_id / dish_name 等），
    只导入目标库中不存在的行，不会覆盖已有数据。
    """
    if not file.filename.endswith('.db'):
        raise HTTPException(status_code=400, detail="仅支持 .db 文件")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    want_tables = [t.strip() for t in tables.split(",") if t.strip()] if tables else ALL_TABLES
    want_tables = [t for t in want_tables if t in ALL_TABLES]

    fd = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    fd.write(content)
    fd.close()

    try:
        merge_result = await merge_app_db_from_file(db, fd.name, tables=want_tables)
        total_imported = merge_result["total_imported"]
        results = merge_result["results"]
        if "dish_stations" in want_tables:
            get_dish_catalog().invalidate()
        _audit("import_db", ",".join(want_tables), f"total_imported={total_imported}")
        await _broadcast_admin_event("import_db", ",".join(want_tables), total_imported=total_imported)
        return {"success": True, "total_imported": total_imported, "results": results}

    except Exception as e:
        logger.error(f"执行导入失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        import os
        os.unlink(fd.name)


@router.get("/scraper-health")
async def get_scraper_health():
    """采集与对账健康状态（data/scraper_health.json）。"""
    from services.scraper_health import read_health
    from services.reconcile_job import is_reconcile_running

    health = read_health()
    health["reconcile_running"] = is_reconcile_running()
    return {"success": True, "health": health}


@router.get("/reconcile-status")
async def get_reconcile_status():
    """对账运行中的实时进度（进程内存态，配合 admin topic 的 nudge 轮询）。"""
    from services.reconcile_job import get_reconcile_progress

    return {"success": True, "progress": get_reconcile_progress()}


@router.get("/reconcile-result")
async def get_reconcile_result(date: str = Query(..., description="营业日 YYYY-MM-DD")):
    """读取某营业日已落盘的对账结果（含完整差异明细）。"""
    import json

    report_path = Path(settings.DATABASE_DIR) / "reconcile" / f"reconcile_{date}.json"
    if not report_path.exists():
        return {"success": False, "error": "该日期尚无对账报告"}
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"报告读取失败: {exc}")
    return {"success": True, "result": data}


@router.post("/reconcile")
async def trigger_reconcile(body: ReconcileRequest, db=Depends(get_db)):
    """后台触发已结账单对账。"""
    from main import restaurant_scraper
    from services.reconcile_job import execute_reconcile, is_reconcile_running

    if is_reconcile_running():
        raise HTTPException(status_code=409, detail="对账任务正在运行中")
    if restaurant_scraper is None:
        raise HTTPException(status_code=503, detail="爬虫未就绪")

    async def _run():
        try:
            await execute_reconcile(
                db,
                restaurant_scraper,
                body.date,
                fix=body.fix,
                notify=body.notify,
            )
        except Exception as exc:
            logger.error("后台对账失败: %s", exc)

    asyncio.create_task(_run())
    _audit("reconcile", "-", f"date={body.date or 'auto'} fix={body.fix} notify={body.notify}")
    return {
        "success": True,
        "message": "对账任务已启动",
        "date": body.date,
        "fix": body.fix,
        "notify": body.notify,
    }
