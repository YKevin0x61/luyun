#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业微信自动推送 API。"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from api.security import verify_admin_token
from database import get_db, CHINA_TZ, DatabaseManager
from services.wecom_push_service import (
    RenderedMessage,
    ALLOWED_PUSH_TYPES,
    DATA_QUALITY_PUSH_TYPE,
    SALES_REPORT_PUSH_TYPE,
    WECOM_TEXT_BYTE_LIMIT,
    decrypt_webhook_url,
    encrypt_webhook_url,
    expand_messages,
    mask_webhook_url,
    validate_schedule_time,
    validate_webhook_url,
    wecom_push_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/wecom-push",
    tags=["企业微信推送"],
    dependencies=[Depends(verify_admin_token)],
)


class WebhookIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    webhook_url: Optional[str] = Field(None, max_length=500)
    enabled: bool = True
    notes: str = Field("", max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized_name = value.strip()
        if not normalized_name:
            raise ValueError("名称不能为空")
        return normalized_name

    @field_validator("webhook_url")
    @classmethod
    def normalize_webhook_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_webhook_url(value)


class JobIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    webhook_id: int = Field(..., gt=0)
    push_type: str = SALES_REPORT_PUSH_TYPE
    schedule_time: str
    date_range_mode: str = "today"
    station: str = ""
    enabled: bool = True
    notes: str = Field("", max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized_name = value.strip()
        if not normalized_name:
            raise ValueError("名称不能为空")
        return normalized_name

    @field_validator("push_type")
    @classmethod
    def validate_push_type(cls, value: str) -> str:
        normalized = (value or SALES_REPORT_PUSH_TYPE).strip()
        if normalized not in ALLOWED_PUSH_TYPES:
            raise ValueError("推送类型不支持")
        return normalized

    @field_validator("schedule_time")
    @classmethod
    def normalize_schedule_time(cls, value: str) -> str:
        return validate_schedule_time(value)

    @field_validator("date_range_mode")
    @classmethod
    def validate_date_range_mode(cls, value: str) -> str:
        normalized_mode = (value or "today").strip()
        if normalized_mode not in {"today", "yesterday"}:
            raise ValueError("报表日期范围只支持 today 或 yesterday")
        return normalized_mode

    @field_validator("station")
    @classmethod
    def normalize_station(cls, value: str) -> str:
        return (value or "").strip()


class SendTextIn(BaseModel):
    webhook_id: int = Field(..., gt=0)
    content: str = Field(..., min_length=1, max_length=30000)
    push_type: str = SALES_REPORT_PUSH_TYPE

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("发送内容不能为空")
        return value

    @field_validator("push_type")
    @classmethod
    def validate_push_type(cls, value: str) -> str:
        normalized = (value or SALES_REPORT_PUSH_TYPE).strip()
        if normalized not in ALLOWED_PUSH_TYPES:
            raise ValueError("推送类型不支持")
        return normalized


def _safe_webhook(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "webhook_url_masked": row.get("webhook_url_masked", ""),
        "enabled": bool(row.get("enabled")),
        "notes": row.get("notes", ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def _jobs_with_webhooks(db: DatabaseManager) -> list[Dict[str, Any]]:
    jobs = await db.wecom_jobs_all()
    webhooks = {item["id"]: item for item in await db.wecom_webhooks_all()}
    result = []
    for job in jobs:
        webhook = webhooks.get(job.get("webhook_id"))
        result.append({
            **job,
            "enabled": bool(job.get("enabled")),
            "webhook_name": webhook.get("name", "") if webhook else "",
            "webhook_url_masked": webhook.get("webhook_url_masked", "") if webhook else "",
            "webhook_enabled": bool(webhook.get("enabled")) if webhook else False,
        })
    return result


@router.get("/webhooks")
async def list_webhooks(db: DatabaseManager = Depends(get_db)):
    webhooks = await db.wecom_webhooks_all()
    return {"success": True, "webhooks": [_safe_webhook(item) for item in webhooks]}


@router.post("/webhooks")
async def create_webhook(payload: WebhookIn, db: DatabaseManager = Depends(get_db)):
    if not payload.webhook_url:
        raise HTTPException(status_code=400, detail="webhook 地址不能为空")
    new_id = await db.wecom_webhook_create({
        "name": payload.name,
        "webhook_url_encrypted": encrypt_webhook_url(payload.webhook_url),
        "webhook_url_masked": mask_webhook_url(payload.webhook_url),
        "enabled": payload.enabled,
        "notes": payload.notes,
    })
    if not new_id:
        raise HTTPException(status_code=500, detail="创建 webhook 失败")
    row = await db.wecom_webhook_get(new_id)
    return {"success": True, "webhook": _safe_webhook(row)}


@router.put("/webhooks/{webhook_id}")
async def update_webhook(webhook_id: int, payload: WebhookIn, db: DatabaseManager = Depends(get_db)):
    existing = await db.wecom_webhook_get(webhook_id)
    if not existing:
        raise HTTPException(status_code=404, detail="webhook 不存在")
    update_values: Dict[str, Any] = {
        "name": payload.name,
        "enabled": payload.enabled,
        "notes": payload.notes,
    }
    if payload.webhook_url:
        update_values["webhook_url_encrypted"] = encrypt_webhook_url(payload.webhook_url)
        update_values["webhook_url_masked"] = mask_webhook_url(payload.webhook_url)
    ok = await db.wecom_webhook_update(webhook_id, update_values)
    if not ok:
        raise HTTPException(status_code=500, detail="更新 webhook 失败")
    row = await db.wecom_webhook_get(webhook_id)
    return {"success": True, "webhook": _safe_webhook(row)}


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: int, db: DatabaseManager = Depends(get_db)):
    jobs = await db.wecom_jobs_all()
    if any(int(job.get("webhook_id") or 0) == webhook_id for job in jobs):
        raise HTTPException(status_code=400, detail="该 webhook 已被任务使用，请先删除或调整任务")
    ok = await db.wecom_webhook_delete(webhook_id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除 webhook 失败")
    return {"success": True}


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: int, db: DatabaseManager = Depends(get_db)):
    try:
        result = await wecom_push_service.send_test_message(db, webhook_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.post("/send-text")
async def send_text(payload: SendTextIn, db: DatabaseManager = Depends(get_db)):
    webhook = await db.wecom_webhook_get(payload.webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="webhook 不存在")
    if not webhook.get("enabled"):
        raise HTTPException(status_code=400, detail="webhook 已停用")

    rendered_message = RenderedMessage(
        content=payload.content,
        byte_length=len(payload.content.encode("utf-8")),
    )
    webhook_url = decrypt_webhook_url(webhook["webhook_url_encrypted"])
    status = "success"
    response_text = ""
    error = ""
    try:
        ok, response_text = await wecom_push_service.send_text_chunks(webhook_url, payload.content)
        if not ok:
            status = "failed"
            error = response_text or "企业微信返回失败"
    except Exception as exc:
        status = "failed"
        error = str(exc)

    await db.wecom_log_add({
        "job_id": None,
        "webhook_id": webhook["id"],
        "webhook_name": webhook.get("name", ""),
        "push_type": payload.push_type,
        "status": status,
        "message_bytes": rendered_message.byte_length,
        "error": error,
        "response_text": response_text,
        "sent_at": datetime.now(CHINA_TZ).isoformat(),
    })
    return {
        "success": status == "success",
        "status": status,
        "message_bytes": rendered_message.byte_length,
        "error": error,
        "response_text": response_text,
    }


@router.get("/jobs")
async def list_jobs(db: DatabaseManager = Depends(get_db)):
    return {"success": True, "jobs": await _jobs_with_webhooks(db)}


@router.post("/jobs")
async def create_job(payload: JobIn, db: DatabaseManager = Depends(get_db)):
    webhook = await db.wecom_webhook_get(payload.webhook_id)
    if not webhook:
        raise HTTPException(status_code=400, detail="webhook 不存在")
    new_id = await db.wecom_job_create(payload.model_dump())
    if not new_id:
        raise HTTPException(status_code=500, detail="创建推送任务失败")
    job = await db.wecom_job_get(new_id)
    return {"success": True, "job": job}


@router.put("/jobs/{job_id}")
async def update_job(job_id: int, payload: JobIn, db: DatabaseManager = Depends(get_db)):
    existing = await db.wecom_job_get(job_id)
    if not existing:
        raise HTTPException(status_code=404, detail="推送任务不存在")
    webhook = await db.wecom_webhook_get(payload.webhook_id)
    if not webhook:
        raise HTTPException(status_code=400, detail="webhook 不存在")
    ok = await db.wecom_job_update(job_id, payload.model_dump())
    if not ok:
        raise HTTPException(status_code=500, detail="更新推送任务失败")
    return {"success": True, "job": await db.wecom_job_get(job_id)}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, db: DatabaseManager = Depends(get_db)):
    ok = await db.wecom_job_delete(job_id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除推送任务失败")
    return {"success": True}


@router.post("/jobs/{job_id}/preview")
async def preview_job(job_id: int, db: DatabaseManager = Depends(get_db)):
    try:
        rendered_message = await wecom_push_service.preview_job(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    parts = list(rendered_message.parts) or [rendered_message.content]
    chunk_count = len(expand_messages(parts))
    return {
        "success": True,
        "content": rendered_message.content,
        "byte_length": rendered_message.byte_length,
        "limit": rendered_message.limit,
        "within_limit": rendered_message.byte_length <= rendered_message.limit,
        "chunk_count": chunk_count,
        "part_count": len(parts),
    }


@router.post("/jobs/{job_id}/send-now")
async def send_job_now(job_id: int, db: DatabaseManager = Depends(get_db)):
    try:
        return await wecom_push_service.send_job(db, job_id, mark_sent=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/logs")
async def list_logs(
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(get_db),
):
    return {"success": True, "logs": await db.wecom_logs_recent(limit)}


@router.get("/meta")
async def get_meta():
    return {
        "success": True,
        "push_types": [
            {"id": SALES_REPORT_PUSH_TYPE, "name": "销售报表文字版"},
            {"id": DATA_QUALITY_PUSH_TYPE, "name": "数据质量告警"},
        ],
        "date_range_modes": [
            {"id": "today", "name": "当天"},
            {"id": "yesterday", "name": "昨天"},
        ],
        "text_limit": WECOM_TEXT_BYTE_LIMIT,
        "job_templates": [
            {
                "id": "sales_report_daily",
                "push_type": SALES_REPORT_PUSH_TYPE,
                "name": "每日销售报表",
                "schedule_time": "21:30",
                "date_range_mode": "today",
            },
            {
                "id": "data_quality_daily",
                "push_type": DATA_QUALITY_PUSH_TYPE,
                "name": "数据质量日报",
                "schedule_time": "22:10",
                "date_range_mode": "today",
                "notes": "建议在日终对账（22:05）之后推送",
            },
        ],
    }
