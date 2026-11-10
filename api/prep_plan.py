#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货计划 API（ADR 0117 重建）。

两条 router，形态照 `api/recipes.py`：

- `reader_router` —— **任一身份**（管理端会话或员工会话）。今天这张清单、首页卡片、
  员工选档口都在这一侧：师傅要能自己打开看参考值。

  ADR 0121 起**不登记产量、也不维护在库量**：页面给的是「今天参考值」，
  师傅拿它减掉自己看到的实物库存，决定还要备多少。补货 / 复盘 / 报废三个端点随之退场。
- `router` —— 管理端（注册处挂 `verify_admin_token`，见 `main.py`）。重算与改主数据
  归属属于「改全店的数」，员工不做。

新增端点时先想清楚：这条是「看/勾自己那档」还是「改全店」。
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.security import require_any_identity_session, verify_admin_token
from services.identity.capabilities import CAP_PREP_REVIEW, has_cap
from database import CHINA_TZ, get_db
from services.prep_plan_context import prep_plan_context
from services.prep_plan_service import prep_plan_service
from services.prep_review import events as prep_review_events
from services.prep_review.summary import review_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prep-plan", tags=["备货计划"])
reader_router = APIRouter(
    prefix="/api/prep-plan",
    tags=["备货计划"],
    dependencies=[Depends(require_any_identity_session)],
)

_ADMIN_WRITE = [Depends(verify_admin_token)]

# 「任一身份进门、端点里再判管理员」的写面。必须单独一个 router：`router` 在
# main.py 注册时整体挂了 `verify_admin_token`，员工会话会先吃 401，而备注要求
# 员工拿到 403 —— 得先过「任一身份」门拿到 `kind` 才能分得清。
manager_router = APIRouter(
    prefix="/api/prep-plan",
    tags=["备货计划"],
    dependencies=[Depends(require_any_identity_session)],
)


def _staff(identity: dict) -> tuple[Optional[int], str]:
    """员工身份返回 (员工 id, 姓名)；管理端返回 (None, "")。"""
    if identity.get("kind") != "staff":
        return None, ""
    employee = identity.get("employee") or {}
    employee_id = int(employee["id"]) if employee.get("id") is not None else None
    return employee_id, str(employee.get("name") or "")


def _wrap(exc: Exception, action: str) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail="只能操作自己档口的备货项")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("%s失败", action)
    return HTTPException(status_code=500, detail=f"{action}失败: {exc}")


# --------------------------------------------------------------------- 读面


@reader_router.get("/today")
async def get_today(
    station: Optional[str] = Query(None, description="档口 id；员工身份忽略（只看自己那档）"),
    hours: Optional[int] = Query(
        None, description="参考值的时间跨度（小时）：24 / 48 / 72 / 168，默认 24"
    ),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    try:
        employee_id, _name = _staff(identity)
        return await prep_plan_service.get_today(
            db, station=station, employee_id=employee_id, horizon_hours=hours
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap(exc, "读取今天备货清单")


@reader_router.get("/summary")
async def get_summary(db=Depends(get_db)):
    try:
        return await prep_plan_service.get_summary(db)
    except Exception as exc:
        raise _wrap(exc, "读取备货汇总")


@manager_router.get("/snapshots")
async def get_snapshots(
    day: str = Query(..., description="YYYY-MM-DD"),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """某一天的参考值快照 + 实际销量（ADR 0123）+ 当天环境信号，只读。

    管理端直接可读；员工会话要「备货复盘」这一项（只读，2026-11-10 用户裁定）。
    """
    _require_review_reader(identity)
    try:
        result = await prep_plan_service.get_snapshot_day(db, day)
    except Exception as exc:
        raise _wrap(exc, "读取参考值快照")
    # 环境信号是解释项，不是快照的主体：读失败只让这一栏为 null，页面照常打开。
    try:
        result["context"] = await prep_plan_context.get_context(db, day)
    except Exception:
        logger.exception("读取备货复盘环境信号失败 day=%s", day)
        result["context"] = None
    return result


@manager_router.get("/review/summary")
async def get_review_summary(
    end: Optional[str] = Query(None, description="YYYY-MM-DD，缺省今天"),
    days: int = Query(90, ge=1, le=90, description="回看天数 1..90"),
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """复盘聚合：偏差口径 / 分组对比 / 多备代价，只读。

    管理端直接可读；员工会话要「备货复盘」这一项（只读，2026-11-10 用户裁定）。

    口径（07:00 主口径、final_qty 优先、无实际的日子不算）见
    `services/prep_review/summary.py` 的模块 docstring；这里只做参数与错误映射。
    """
    _require_review_reader(identity)
    try:
        return await review_summary(db, end=end, days=days)
    except Exception as exc:
        raise _wrap(exc, "读取备货复盘汇总")


class ContextNoteRequest(BaseModel):
    day: Optional[str] = None
    note: str = ""


@manager_router.put("/context/note")
async def put_context_note(
    payload: ContextNoteRequest,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """写某天的人工备注（缺省今天）。员工 403。

    这里没有用 `_ADMIN_WRITE`：`verify_admin_token` 对员工会话只会给 401，
    而票面要求「员工身份必须 403」。所以先过「任一身份」门拿到身份，再在
    这里把员工挡成 403。代价是纯 API Token 调用方（不带会话 cookie）会被
    401 拒绝——管理端页面走 cookie，不受影响。
    """
    if identity.get("kind") == "staff":
        raise HTTPException(status_code=403, detail="员工不能修改备货复盘备注")

    day = (payload.day or datetime.now(CHINA_TZ).date().isoformat()).strip()
    try:
        day = datetime.strptime(day, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="day 必须是 YYYY-MM-DD")
    try:
        context = await prep_plan_context.set_note(db, day, payload.note)
    except Exception as exc:
        raise _wrap(exc, "保存备货复盘备注")
    return {"success": True, "day": day, "context": context}


class WeatherSettingsRequest(BaseModel):
    api_key: Optional[str] = None
    city: str = ""
    api_host: Optional[str] = None


def _reject_staff(identity: dict, action: str) -> None:
    if identity.get("kind") == "staff":
        raise HTTPException(status_code=403, detail=f"员工不能{action}")


def _require_review_reader(identity: dict) -> None:
    """备货复盘的只读面：管理端直接放行；员工会话要「备货复盘」这一项（只读）。

    2026-11-10 用户裁定：管理端专属页开只读给勾了该项能力的员工会话；
    普通员工仍 403（与写面 `_reject_staff` 同一档拒绝口径）。
    """
    if identity.get("kind") != "staff":
        return
    employee = identity.get("employee") or {}
    if not has_cap(employee.get("admin_caps"), CAP_PREP_REVIEW):
        raise HTTPException(status_code=403, detail="没有「备货复盘」权限")


@manager_router.get("/context/weather")
async def get_weather_settings(
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """天气采集配置：Key 只回脱敏值，明文永不出服务端。"""
    _reject_staff(identity, "查看天气配置")
    try:
        return await prep_plan_context.read_weather_config(db)
    except Exception as exc:
        raise _wrap(exc, "读取天气配置")


@manager_router.put("/context/weather")
async def put_weather_settings(
    payload: WeatherSettingsRequest,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """保存 Key / 城市 / Host。空 Key = 不改；任一关键项变化即清 location_id。"""
    _reject_staff(identity, "修改天气配置")
    try:
        return await prep_plan_context.save_weather_config(
            db,
            api_key=payload.api_key,
            city=payload.city,
            api_host=payload.api_host,
        )
    except Exception as exc:
        raise _wrap(exc, "保存天气配置")


@manager_router.post("/context/weather/test")
async def test_weather_settings(
    payload: WeatherSettingsRequest,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """用当前表单跑一次 GEO + 今日预报，不写库。

    「Key 不对」是业务结果，不是服务器故障：统一 200 + `{ok: false, error}`，
    只有参数不合法（城市为空、Host 不是 https）才 400。
    """
    _reject_staff(identity, "测试天气配置")
    try:
        return await prep_plan_context.test_weather_config(
            db,
            api_key=payload.api_key,
            city=payload.city,
            api_host=payload.api_host,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise _wrap(exc, "测试天气配置")


@reader_router.get("/items")
async def list_items(db=Depends(get_db)):
    try:
        return {"success": True, "items": await prep_plan_service.list_items(db)}
    except Exception as exc:
        raise _wrap(exc, "读取备货品主数据")


class StationPickRequest(BaseModel):
    station: str


@reader_router.get("/staff-station")
async def get_staff_station(
    identity: dict = Depends(require_any_identity_session), db=Depends(get_db)
):
    try:
        employee_id, _name = _staff(identity)
        if employee_id is None:
            return {"success": True, "station": None, "staff": False}
        station = await prep_plan_service.get_staff_station(db, employee_id)
        return {"success": True, "station": station, "staff": True}
    except Exception as exc:
        raise _wrap(exc, "读取员工档口")


@reader_router.post("/staff-station")
async def set_staff_station(
    payload: StationPickRequest,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    try:
        employee_id, _name = _staff(identity)
        if employee_id is None:
            raise HTTPException(status_code=400, detail="只有员工身份需要选档口")
        return await prep_plan_service.set_staff_station(db, employee_id, payload.station)
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap(exc, "选择员工档口")


class UpdateEntryRequest(BaseModel):
    final_qty: Optional[float] = None
    done: Optional[bool] = None
    # 「为什么改数量」的可选原因：键是数据面契约（`MANUAL_EDIT_REASONS`），
    # 中文标签在前端（`prepPlanCopy.js`）。不选就是 None，与改造前一致。
    reason: Optional[str] = None


@reader_router.patch("/today/entries/{entry_id}")
async def update_entry(
    entry_id: int,
    payload: UpdateEntryRequest,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """改数量或勾选。默认带建议量、能改但不强制（ADR 0117）。"""
    reason = payload.reason
    if reason is not None:
        # 空串按「没选」处理（前端不选时根本不带这个键；老客户端也不该因为空串 400）。
        reason = str(reason).strip() or None
        if reason is not None and reason not in prep_review_events.MANUAL_EDIT_REASONS:
            raise HTTPException(
                status_code=400,
                detail="改数量的原因只能是 实盘库存 / 临时加单 / 经验判断 / 其他",
            )
    try:
        employee_id, done_by = _staff(identity)
        return await prep_plan_service.update_entry(
            db,
            entry_id,
            final_qty=payload.final_qty,
            done=payload.done,
            done_by=done_by,
            employee_id=employee_id,
            reason=reason,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _wrap(exc, "更新备货项")


class PlanEventRequest(BaseModel):
    """复制清单埋点。`kind` 只收 `plan_copied`（见 `services/prep_review/events.py`）。"""

    kind: Optional[str] = None
    meta: Optional[dict] = None


@manager_router.post("/events")
async def record_plan_event(
    payload: PlanEventRequest,
    identity: dict = Depends(require_any_identity_session),
    db=Depends(get_db),
):
    """记一条现场埋点（当前只有「复制清单」）。

    管理端专属：员工拿到 403（不是 401 —— 他有会话，只是这条路不对他开放）。
    与 `record_event` 的其余调用点一样，写失败只记日志、仍回 200：埋点是旁路证据，
    页面 fire-and-forget，失败不该影响复制这个动作本身。
    """
    if identity.get("kind") == "staff":
        raise HTTPException(status_code=403, detail="员工不能记录备货埋点")

    kind = str(payload.kind or "").strip()
    if kind != prep_review_events.PLAN_COPIED:
        raise HTTPException(
            status_code=400, detail="这个端点只收复制清单事件（kind=plan_copied）"
        )
    now = datetime.now(CHINA_TZ)
    meta = payload.meta if isinstance(payload.meta, dict) else {}
    recorded = await prep_review_events.record_event(
        db,
        at=now.isoformat(),
        plan_day=now.date().isoformat(),
        kind=prep_review_events.PLAN_COPIED,
        meta=meta,
    )
    return {"success": True, "recorded": recorded}


# --------------------------------------------------------------------- 管理端


@router.post("/today/recompute", dependencies=_ADMIN_WRITE)
async def recompute_today(
    hours: Optional[int] = Query(
        None, description="参考值的时间跨度（小时）：24 / 48 / 72 / 168，默认 24"
    ),
    db=Depends(get_db),
):
    """按当前时刻重算建议量。手改过的数与勾选状态不动。"""
    try:
        return await prep_plan_service.get_today(db, horizon_hours=hours)
    except Exception as exc:
        raise _wrap(exc, "重算备货清单")


class UpdateItemRequest(BaseModel):
    kind: Optional[str] = None
    station: Optional[str] = None


@router.patch("/items/{item_id}", dependencies=_ADMIN_WRITE)
async def update_item(item_id: int, payload: UpdateItemRequest, db=Depends(get_db)):
    """改「半成品 / 原料」归属或所属档口（ADR 0117：系统先猜，人在页面上改）。"""
    try:
        return await prep_plan_service.update_item(
            db, item_id, kind=payload.kind, station=payload.station
        )
    except Exception as exc:
        raise _wrap(exc, "更新备货品")
