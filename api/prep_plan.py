#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
备货计划 API。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Any, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from database import get_db, CHINA_TZ
from services.prep_plan_service import prep_plan_service
from api.security import verify_admin_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/prep-plan", tags=["备货计划"])
_ADMIN_WRITE = [Depends(verify_admin_token)]




class GeneratePlanRequest(BaseModel):
    target_start: Optional[str] = None
    target_end: Optional[str] = None
    method: str = "weighted_history"
    created_by: str = ""
    station: Optional[str] = None


class CreateBatchRequest(BaseModel):
    item_name: str = Field(..., min_length=1)
    produced_qty: float = Field(..., gt=0)
    unit: str = Field("", min_length=0)
    produced_at: Optional[str] = None
    operator: str = ""
    notes: str = ""


class UpdateBatchRequest(BaseModel):
    remaining_qty: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    operator: str = ""


class CreateMovementRequest(BaseModel):
    batch_id: int = Field(..., gt=0)
    item_name: str = Field(..., min_length=1)
    unit: str = ""
    movement_type: str
    qty_delta: float
    reason: str = ""
    operator: str = ""
    source_type: str = ""
    source_id: str = ""


@router.get("/forecast")
async def get_forecast(
    target_start: Optional[str] = Query(None, description="ISO 时间，默认 now()"),
    target_end: Optional[str] = Query(None, description="ISO 时间，默认 target_start + 24h"),
    station: Optional[str] = Query(None, description="档口 ID，禁止 loumian"),
    db=Depends(get_db),
):
    try:
        result = await prep_plan_service.compute_forecast(
            db=db,
            target_start=target_start,
            target_end=target_end,
            station=station,
            include_inventory=False,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("forecast 计算失败")
        raise HTTPException(status_code=500, detail=f"预测计算失败: {exc}")


@router.post("/generate", dependencies=_ADMIN_WRITE)
async def generate_plan(payload: GeneratePlanRequest, db=Depends(get_db)):
    try:
        result = await prep_plan_service.create_plan_run(
            db=db,
            target_start=payload.target_start,
            target_end=payload.target_end,
            method=payload.method,
            created_by=payload.created_by,
            station=payload.station,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("生成计划失败")
        raise HTTPException(status_code=500, detail=f"生成计划失败: {exc}")


@router.get("/current")
async def get_current_plan(db=Depends(get_db)):
    try:
        now_iso = datetime.now(CHINA_TZ).isoformat()
        runs_tdb = db.table("prep_plan_runs")
        items_tdb = db.table("prep_plan_items")

        async with runs_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT *
                FROM prep_plan_runs
                WHERE target_start <= ? AND target_end > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (now_iso, now_iso),
            )
            run_row = await cursor.fetchone()
            if not run_row:
                await cursor.execute(
                    """
                    SELECT *
                    FROM prep_plan_runs
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
                run_row = await cursor.fetchone()
                if not run_row:
                    return {"success": True, "run": None, "items": [], "missing_rules": []}

        run = dict(run_row)
        run_id = run["id"]
        async with items_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT *
                FROM prep_plan_items
                WHERE run_id = ?
                ORDER BY station ASC, position ASC, recommended_qty DESC, item_name ASC
                """,
                (run_id,),
            )
            items = [dict(row) for row in await cursor.fetchall()]

        return {
            "success": True,
            "run": run,
            "items": items,
            "missing_rules": [],
        }
    except Exception as exc:
        logger.exception("查询当前计划失败")
        raise HTTPException(status_code=500, detail=f"查询当前计划失败: {exc}")


@router.post("/batches", dependencies=_ADMIN_WRITE)
async def create_batch(payload: CreateBatchRequest, db=Depends(get_db)):
    try:
        prep_items_tdb = db.table("prep_items")
        batches_tdb = db.table("prep_batches")
        movements_tdb = db.table("prep_stock_movements")

        async with prep_items_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, item_name, unit, shelf_life_hours
                FROM prep_items
                WHERE item_name = ? AND unit = ? AND active = 1
                LIMIT 1
                """,
                (payload.item_name, payload.unit),
            )
            prep_item = await cursor.fetchone()
        if not prep_item:
            raise HTTPException(status_code=400, detail=f"备货品不存在或未启用: {payload.item_name} ({payload.unit})")

        produced_dt = datetime.now(CHINA_TZ) if not payload.produced_at else datetime.fromisoformat(
            payload.produced_at.replace("Z", "+00:00")
        ).astimezone(CHINA_TZ)
        now_iso = datetime.now(CHINA_TZ).isoformat()
        shelf_life_hours = float(prep_item["shelf_life_hours"] or 24)
        expires_dt = produced_dt + timedelta(hours=shelf_life_hours)

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_batches (
                    prep_item_id, item_name, produced_qty, remaining_qty, unit,
                    produced_at, expires_at, status, operator, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    prep_item["id"],
                    payload.item_name,
                    float(payload.produced_qty),
                    float(payload.produced_qty),
                    payload.unit,
                    produced_dt.isoformat(),
                    expires_dt.isoformat(),
                    payload.operator or "",
                    payload.notes or "",
                    now_iso,
                    now_iso,
                ),
            )
            batch_id = cursor.lastrowid
        await batches_tdb.commit()

        async with movements_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_stock_movements (
                    batch_id, prep_item_id, item_name, unit, movement_type, qty_delta,
                    reason, operator, source_type, source_id, created_at
                ) VALUES (?, ?, ?, ?, 'produce', ?, 'batch_create', ?, 'batch', ?, ?)
                """,
                (
                    batch_id,
                    prep_item["id"],
                    payload.item_name,
                    payload.unit,
                    float(payload.produced_qty),
                    payload.operator or "",
                    str(batch_id),
                    now_iso,
                ),
            )
        await movements_tdb.commit()

        return {
            "success": True,
            "batch_id": batch_id,
            "expires_at": expires_dt.isoformat(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("创建备货批次失败")
        raise HTTPException(status_code=500, detail=f"创建备货批次失败: {exc}")


@router.patch("/batches/{batch_id}", dependencies=_ADMIN_WRITE)
async def update_batch(batch_id: int, payload: UpdateBatchRequest, db=Depends(get_db)):
    try:
        batches_tdb = db.table("prep_batches")
        movements_tdb = db.table("prep_stock_movements")
        now_iso = datetime.now(CHINA_TZ).isoformat()

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM prep_batches WHERE id = ? LIMIT 1",
                (batch_id,),
            )
            current = await cursor.fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="批次不存在")
        current_dict = dict(current)

        update_fields = []
        update_values: List[Any] = []

        if payload.remaining_qty is not None:
            new_qty = float(payload.remaining_qty)
            old_qty = float(current_dict["remaining_qty"] or 0)
            delta = new_qty - old_qty
            if abs(delta) > 1e-9:
                async with movements_tdb.conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO prep_stock_movements (
                            batch_id, prep_item_id, item_name, unit, movement_type, qty_delta,
                            reason, operator, source_type, source_id, created_at
                        ) VALUES (?, ?, ?, ?, 'adjust', ?, 'batch_update', ?, 'batch', ?, ?)
                        """,
                        (
                            batch_id,
                            current_dict.get("prep_item_id"),
                            current_dict["item_name"],
                            current_dict["unit"],
                            delta,
                            payload.operator or "",
                            str(batch_id),
                            now_iso,
                        ),
                    )
                await movements_tdb.commit()
            update_fields.append("remaining_qty = ?")
            update_values.append(new_qty)

        if payload.status is not None:
            update_fields.append("status = ?")
            update_values.append(payload.status)

        if payload.notes is not None:
            update_fields.append("notes = ?")
            update_values.append(payload.notes)

        update_fields.append("updated_at = ?")
        update_values.append(now_iso)
        update_values.append(batch_id)

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE prep_batches SET {', '.join(update_fields)} WHERE id = ?",
                tuple(update_values),
            )
            affected = cursor.rowcount
        await batches_tdb.commit()

        return {"success": True, "affected": affected}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("更新备货批次失败")
        raise HTTPException(status_code=500, detail=f"更新备货批次失败: {exc}")


@router.get("/movements")
async def get_movements(
    item_name: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db=Depends(get_db),
):
    try:
        tdb = db.table("prep_stock_movements")
        conditions = []
        params: List[Any] = []
        if item_name:
            conditions.append("item_name = ?")
            params.append(item_name)
        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
            conditions.append("created_at >= ?")
            params.append(start_dt.isoformat())
        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ) + timedelta(days=1)
            conditions.append("created_at < ?")
            params.append(end_dt.isoformat())
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT *
                FROM prep_stock_movements
                {where_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                tuple(params + [limit]),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return {"success": True, "items": rows, "count": len(rows)}
    except Exception as exc:
        logger.exception("查询库存流水失败")
        raise HTTPException(status_code=500, detail=f"查询库存流水失败: {exc}")


@router.post("/movements", dependencies=_ADMIN_WRITE)
async def create_movement(payload: CreateMovementRequest, db=Depends(get_db)):
    try:
        allowed_types = {"produce", "discard", "adjust", "expire"}
        if payload.movement_type not in allowed_types:
            raise HTTPException(status_code=400, detail=f"不支持的 movement_type: {payload.movement_type}")
        if payload.movement_type == "produce" and payload.qty_delta <= 0:
            raise HTTPException(status_code=400, detail="produce 的 qty_delta 必须 > 0")
        if payload.movement_type in {"discard", "expire"} and payload.qty_delta >= 0:
            raise HTTPException(status_code=400, detail=f"{payload.movement_type} 的 qty_delta 必须 < 0")

        batches_tdb = db.table("prep_batches")
        movements_tdb = db.table("prep_stock_movements")
        now_iso = datetime.now(CHINA_TZ).isoformat()

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM prep_batches WHERE id = ? LIMIT 1", (payload.batch_id,))
            batch = await cursor.fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        batch_dict = dict(batch)

        async with movements_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO prep_stock_movements (
                    batch_id, prep_item_id, item_name, unit, movement_type, qty_delta,
                    reason, operator, source_type, source_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.batch_id,
                    batch_dict.get("prep_item_id"),
                    payload.item_name,
                    payload.unit,
                    payload.movement_type,
                    float(payload.qty_delta),
                    payload.reason or "",
                    payload.operator or "",
                    payload.source_type or "",
                    payload.source_id or "",
                    now_iso,
                ),
            )
            movement_id = cursor.lastrowid
        await movements_tdb.commit()

        old_remaining = float(batch_dict["remaining_qty"] or 0)
        new_remaining = old_remaining + float(payload.qty_delta)
        if new_remaining < 0:
            new_remaining = 0.0

        new_status = batch_dict["status"]
        if new_remaining <= 0 and new_status in {"active", "near_expiry"}:
            new_status = "active"  # MVP2 先不启用 used_up
        if payload.movement_type == "discard":
            new_status = "discarded"
        if payload.movement_type == "expire":
            new_status = "expired"

        async with batches_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE prep_batches
                SET remaining_qty = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_remaining, new_status, now_iso, payload.batch_id),
            )
        await batches_tdb.commit()

        return {
            "success": True,
            "movement_id": movement_id,
            "remaining_qty": round(new_remaining, 2),
            "status": new_status,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("新增库存流水失败")
        raise HTTPException(status_code=500, detail=f"新增库存流水失败: {exc}")


@router.get("/expiring")
async def get_expiring_batches(
    within_hours: int = Query(4, ge=1, le=72),
    db=Depends(get_db),
):
    try:
        now_dt = datetime.now(CHINA_TZ)
        end_dt = now_dt + timedelta(hours=within_hours)
        tdb = db.table("prep_batches")

        async with tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT *
                FROM prep_batches
                WHERE status IN ('active', 'near_expiry')
                  AND remaining_qty > 0
                  AND expires_at > ?
                  AND expires_at <= ?
                ORDER BY expires_at ASC
                """,
                (now_dt.isoformat(), end_dt.isoformat()),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return {"success": True, "items": rows, "count": len(rows)}
    except Exception as exc:
        logger.exception("查询临期批次失败")
        raise HTTPException(status_code=500, detail=f"查询临期批次失败: {exc}")


@router.get("/accuracy")
async def get_accuracy(
    start_date: str = Query(...),
    end_date: str = Query(...),
    db=Depends(get_db),
):
    """
    MVP3 基础版准确率：按计划项聚合统计 abs(预测-实际)/实际。
    实际值暂以 orders + semi_rules 换算（简化版：同周期总量）。
    """
    try:
        # 1) 取时间范围内的 plan_items（以 run target_start 落在区间）
        runs_tdb = db.table("prep_plan_runs")
        items_tdb = db.table("prep_plan_items")
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ) + timedelta(days=1)

        async with runs_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, target_start, target_end
                FROM prep_plan_runs
                WHERE target_start >= ? AND target_start < ?
                """,
                (start_dt.isoformat(), end_dt.isoformat()),
            )
            run_rows = await cursor.fetchall()

        if not run_rows:
            return {"success": True, "summary": {"plan_count": 0, "accuracy": None}, "items": []}

        run_ids = [row["id"] for row in run_rows]
        placeholders = ",".join(["?"] * len(run_ids))
        async with items_tdb.conn.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT run_id, item_name, unit, forecast_qty
                FROM prep_plan_items
                WHERE run_id IN ({placeholders})
                """,
                tuple(run_ids),
            )
            plan_items = [dict(row) for row in await cursor.fetchall()]

        # 2) 实际值：按订单 × semi_rules 换算后的真实消耗
        result_items = []
        accuracy_values = []
        for row in run_rows:
            run_start = row["target_start"]
            run_end = row["target_end"]
            actual_totals = await prep_plan_service.compute_actual_consumption_totals(
                db=db,
                target_start=run_start,
                target_end=run_end,
                station=None,
            )
            run_plan_items = [item for item in plan_items if item["run_id"] == row["id"]]
            for item in run_plan_items:
                key = (item["item_name"], item["unit"])
                forecast_qty = float(item["forecast_qty"] or 0)
                actual_qty = float(actual_totals.get(key, 0))
                if actual_qty <= 0:
                    acc = 1.0 if forecast_qty <= 0 else 0.0
                else:
                    acc = 1.0 - abs(forecast_qty - actual_qty) / actual_qty
                    if acc < 0:
                        acc = 0.0
                accuracy_values.append(acc)
                result_items.append(
                    {
                        "run_id": row["id"],
                        "item_name": item["item_name"],
                        "unit": item["unit"],
                        "forecast_qty": round(forecast_qty, 2),
                        "actual_qty": round(actual_qty, 2),
                        "accuracy": round(acc, 4),
                    }
                )

        overall = sum(accuracy_values) / len(accuracy_values) if accuracy_values else None
        return {
            "success": True,
            "summary": {
                "plan_count": len(run_rows),
                "item_count": len(result_items),
                "accuracy": round(overall, 4) if overall is not None else None,
            },
            "items": result_items[:2000],
        }
    except Exception as exc:
        logger.exception("查询准确率失败")
        raise HTTPException(status_code=500, detail=f"查询准确率失败: {exc}")


@router.post("/init-items-from-rules", dependencies=_ADMIN_WRITE)
async def init_items_from_rules(db=Depends(get_db)):
    """
    从 semi_finished_rules 自动补齐 prep_items（仅新增不存在的 item_name+unit）。
    """
    try:
        rules_tdb = db.table("semi_finished_rules")
        items_tdb = db.table("prep_items")
        now_iso = datetime.now(CHINA_TZ).isoformat()

        async with rules_tdb.conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT semi_name, unit, category, position, COUNT(*) AS rule_count
                FROM semi_finished_rules
                GROUP BY semi_name, unit, category, position
                """
            )
            rule_rows = await cursor.fetchall()

        existing_keys = set()
        async with items_tdb.conn.cursor() as cursor:
            await cursor.execute("SELECT item_name, unit FROM prep_items")
            for row in await cursor.fetchall():
                existing_keys.add((row["item_name"], row["unit"] or ""))

        created = 0
        for row in rule_rows:
            item_name = row["semi_name"]
            unit = row["unit"] or ""
            key = (item_name, unit)
            if key in existing_keys:
                continue

            position = row["position"] or ""
            station = ""
            position_station_map = {
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
            station = position_station_map.get(position, "")

            async with items_tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO prep_items (
                        item_name, station, position, category, unit,
                        shelf_life_hours, lead_time_hours, min_batch_qty,
                        safety_stock_ratio, active, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 24, 0, 0, 0.15, 1, 'auto_init_from_rules', ?, ?)
                    """,
                    (
                        item_name,
                        station,
                        position,
                        row["category"] or "",
                        unit,
                        now_iso,
                        now_iso,
                    ),
                )
            created += 1
            existing_keys.add(key)

        await items_tdb.commit()
        return {"success": True, "created": created, "total_rules": len(rule_rows)}
    except Exception as exc:
        logger.exception("初始化 prep_items 失败")
        raise HTTPException(status_code=500, detail=f"初始化 prep_items 失败: {exc}")

