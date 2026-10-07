#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业微信自动推送 API。

鉴权口径（票 06）：这一页的**写操作**只接受浏览器登录会话（``require_session``），
读操作沿用 router 级的 ``verify_admin_token``（会话 / API token / 过渡密钥都行）。
分界线是「会不会改配置」：写接口用 token 调用一律 401 「需要登录」，页面是唯一调用方，
所以没有脚本会因此断掉（spec「鉴权与审计」）。
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from api.security import require_session, verify_admin_token
from database import get_db, CHINA_TZ, DatabaseManager
from services.wecom_push_topics import (
    WECOM_PUSH_API_VERSION,
    PushTrigger,
    all_topics,
    get_topic,
)
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

# 群组重名是「名称已存在」而不是「服务器出错」：repo 层用返回 0 表达（它不复用同名
# 群组，见 `wecom_channel_group_create` 的理由），路由层把它翻成 400 给页面。
_DUPLICATE_GROUP_NAME = "群组名称已存在，请换一个"

# ISO 时间戳 → 页面上那串「2026-10-05 21:30:12」。只留到秒：卡片上不需要毫秒，
# 而 `last_sent_at` 由迁移 0016 搬来的历史行也长得不完全一样（有的没带时区）。
_SENT_AT_DISPLAY = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")


def _sent_at_display(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    matched = _SENT_AT_DISPLAY.match(text)
    return f"{matched.group(1)} {matched.group(2)}" if matched else text


def _topic_name(topic_id: str) -> str:
    """内容类型的显示名；注册表里没有这个 id 就退回 id 本身。

    退回 id 而不是丢掉这一行：库里可能留着某个已下线内容类型的订阅（注册表删了一类
    内容，历史订阅还在），页面要看得见这一行才谈得上清理。
    """
    topic = get_topic(topic_id)
    return topic.name if topic else str(topic_id)



class WebhookIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    webhook_url: Optional[str] = Field(None, max_length=500)
    enabled: bool = True
    # 三态：None = 不动这一列。列表上的「停用 / 启用」快捷开关和旧的前端 bundle（PWA
    # 缓存）都不会带这个字段 —— 给它一个 `False` 默认值，点一下「停用」就会把「卫生群」
    # 标记清掉，而且界面上看不出来。
    hygiene_feed: Optional[bool] = None
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


class ChannelGroupIn(BaseModel):
    """群组的新增 / 改名。``enabled`` 与 ``notes`` 给默认值，改名时可以只发名称。"""

    name: str = Field(..., min_length=1, max_length=60)
    enabled: bool = True
    notes: str = Field("", max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("群组名称不能为空")
        return normalized


class GroupMemberIn(BaseModel):
    channel_id: int = Field(..., gt=0)


class SubscriptionIn(BaseModel):
    """订阅的勾选与取消。

    ``target_channel_id`` 与 ``target_group_id`` 恰好给一个（数据库同款 CHECK）。
    ``enabled`` 只有 POST 用得上：GET 之外的取消勾选走 DELETE（删行），而
    ``enabled=False`` 是**停用**——两者的区别见 `wecom_subscription_delete_for`。
    """

    topic_id: str = Field(..., min_length=1, max_length=60)
    target_channel_id: Optional[int] = Field(None, gt=0)
    target_group_id: Optional[int] = Field(None, gt=0)
    enabled: bool = True

    @field_validator("topic_id")
    @classmethod
    def check_topic(cls, value: str) -> str:
        normalized = (value or "").strip()
        if get_topic(normalized) is None:
            raise ValueError(f"未知的推送内容类型: {normalized}")
        return normalized

    @model_validator(mode="after")
    def check_target(self) -> "SubscriptionIn":
        if (self.target_channel_id is None) == (self.target_group_id is None):
            raise ValueError("订阅必须且只能指定一个目标（渠道或群组）")
        return self


def _safe_webhook(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "webhook_url_masked": row.get("webhook_url_masked", ""),
        "enabled": bool(row.get("enabled")),
        "hygiene_feed": bool(row.get("hygiene_feed")),
        "notes": row.get("notes", ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        # 页面上渠道卡片要显示的三样（票 06）：被几条任务引用（删除提示也要）、
        # 最近一次发送成功的时间。群组与订阅内容在 /subscriptions 那一侧。
        "job_count": int(row.get("job_count") or 0),
        "last_sent_at": row.get("last_sent_at") or "",
    }


async def _channel_last_sent(db: DatabaseManager) -> Dict[int, str]:
    """每个渠道最近一次**成功**投递的完成时间（`{channel_id: ISO}`）。

    成功一次 = 出站表里 status = sent 的那一行。取不到（从未发过）就不在字典里，
    页面上显示「从未发送」。
    """
    rows = await db.wecom_outbox_recent(limit=200, status="sent")
    latest: Dict[int, str] = {}
    for row in rows:
        channel_id = row.get("target_channel_id")
        if channel_id is None:
            continue
        key = int(channel_id)
        if key in latest:
            continue
        # repo 按 created_at 倒序取，第一眼看到的就是最近那条；终态行的 finished_at
        # 才是「发送成功」的时刻，缺了（历史搬入行没有这一列的值）就退回 created_at。
        stamp = str(row.get("finished_at") or row.get("created_at") or "")
        if stamp:
            latest[key] = stamp
    return latest


async def _subscription_targets(db: DatabaseManager) -> Dict[str, Any]:
    """订阅矩阵一次取全，返回 ``{topics, channels}``（页面上两处都读这一份）。

    - ``topics``：行 = 内容类型（注册表顺序），每行带它订阅到的渠道 id
      （``channels``）与 ``contains_employee_photos``；
    - ``channels``：列 = **全部渠道**，每个带启停 / 备注 / 所属群组 / 订阅了哪些内容 /
      最近一次发送成功时间。列取全渠道而不是「只取被订阅到的」：新建但还没勾过订阅的
      渠道也要出现在矩阵上（否则没有可勾的位置），而渠道卡片本来就要显示未被订阅的群。
    """
    subscriptions = await db.wecom_subscriptions_all()
    groups = {
        int(group["id"]): group for group in await db.wecom_channel_groups_all()
    }
    members_map = await db.wecom_channel_group_members_map()

    # 每个渠道订阅了哪些内容类型：直接订阅 + 经由群组（两种来路都要在卡片上看得出来）。
    by_channel: Dict[int, Dict[str, Any]] = {}
    for subscription in subscriptions:
        if not subscription.get("enabled"):
            continue  # 停用的订阅不算「订阅了这类内容」
        topic_id = str(subscription["topic_id"])
        group_id = subscription.get("target_group_id")
        if group_id is None:
            entries = [(int(subscription["target_channel_id"]), False)]
        else:
            group = groups.get(int(group_id))
            if group is None or not group.get("enabled"):
                continue  # 群组停用：这一组订阅暂停（与 resolve_targets 同一口径）
            entries = [
                (int(member["channel_id"]), True)
                for member in members_map.get(int(group_id), [])
            ]
        for channel_id, via_group in entries:
            item = by_channel.setdefault(channel_id, {"topics": {}, "groups": set()})
            existing = item["topics"].get(topic_id)
            # 一条订阅可以同时命中（渠道直接订阅 + 所属群组订阅）：直接订阅那条
            # 说了算，`via_group` 只在**仅**经群组命中时为真。
            if existing is None or (existing["via_group"] and not via_group):
                item["topics"][topic_id] = {"via_group": via_group}
            if via_group:
                item["groups"].add(int(group_id))

    # 渠道所属的群组还有一条来路：成员关系本身（没被订阅的群组也要显示在卡片上）。
    for group_id, members in members_map.items():
        group = groups.get(group_id)
        if group is None:
            continue
        for member in members:
            channel_id = int(member["channel_id"])
            by_channel.setdefault(channel_id, {"topics": {}, "groups": set()})["groups"].add(group_id)

    channels = {int(row["id"]): row for row in await db.wecom_webhooks_all()}
    last_sent = await _channel_last_sent(db)

    def channel_payload(channel_id: int) -> Optional[Dict[str, Any]]:
        channel = channels.get(channel_id)
        if channel is None:
            return None
        item = by_channel.get(channel_id, {"topics": {}, "groups": set()})
        return {
            "id": channel_id,
            "name": channel.get("name", ""),
            "enabled": bool(channel.get("enabled")),
            "notes": channel.get("notes", ""),
            "webhook_url_masked": channel.get("webhook_url_masked", ""),
            "job_count": int(channel.get("job_count") or 0),
            "last_sent_at": last_sent.get(channel_id, ""),
            # 渠道卡片显示的两样：所属群组与订阅了哪些内容（含「经群组来的」这一层）。
            # 名字在这里就取好，页面不必再拿 id 去两张表里对照。
            "groups": [
                {
                    "id": group_id,
                    "name": groups.get(group_id, {}).get("name", ""),
                    "enabled": bool(groups.get(group_id, {}).get("enabled")),
                }
                for group_id in sorted(item["groups"])
            ],
            "topics": [
                {
                    "id": topic_id,
                    "name": _topic_name(topic_id),
                    "via_group": entry["via_group"],
                }
                for topic_id, entry in sorted(item["topics"].items())
            ],
        }

    def topic_payload(topic, subscribed_channel_ids: List[int]) -> Dict[str, Any]:
        return {
            "id": topic.id,
            "name": topic.name,
            "triggers": [trigger.value for trigger in topic.triggers_in_order],
            "contains_employee_photos": topic.contains_employee_photos,
            "default_schedule_time": topic.default_schedule_time,
            # `{id, enabled}` 而不是 id 数组：页面要判「订阅了但渠道全停着」——
            # 那等于一条都不发，与零订阅同一种要亮出来的状态（用户故事 9）。
            "channels": [
                {
                    "id": channel_id,
                    "enabled": bool(channels[channel_id].get("enabled")),
                }
                for channel_id in sorted(subscribed_channel_ids)
            ],
        }

    rows = []
    for topic in all_topics():
        channel_ids = [
            channel_id
            for channel_id, item in by_channel.items()
            if topic.id in item["topics"] and channel_id in channels
        ]
        rows.append(topic_payload(topic, channel_ids))

    # 列：全部渠道，按名称排序（矩阵的列序与群组下拉的选项序一致）。
    column_ids = sorted(channels, key=lambda cid: str(channels[cid].get("name", "")))
    return {
        "topics": rows,
        "channels": [
            payload
            for payload in (channel_payload(cid) for cid in column_ids)
            if payload is not None
        ],
    }


async def _channel_card(db: DatabaseManager, channel_id: int) -> Optional[Dict[str, Any]]:
    """单个渠道的**最新**卡片形状，与 /webhooks 的 ``channels`` 同一份口径。

    新建 / 改名 / 启停之后把它回给页面：省掉一次整页重拉，也保证卡片上那几样
    （所属群组、订阅内容、最近一次发送成功）不会出现「本地拼的」与「拉来的」两份口径。
    """
    matrix = await _subscription_targets(db)
    for channel in matrix["channels"]:
        if int(channel["id"]) == int(channel_id):
            return channel
    return None


def _group_payload(group: Dict[str, Any], members: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": int(group["id"]),
        "name": group.get("name", ""),
        "enabled": bool(group.get("enabled")),
        "notes": group.get("notes", ""),
        "member_channel_ids": sorted(int(m["channel_id"]) for m in members),
    }


async def _groups_with_members(db: DatabaseManager) -> List[Dict[str, Any]]:
    members_map = await db.wecom_channel_group_members_map()
    return [
        _group_payload(group, members_map.get(int(group["id"]), []))
        for group in await db.wecom_channel_groups_all()
    ]


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
    """渠道列表。

    **票 06 改了形状**：原来是 ``{"success", "webhooks"}`` 一个键，现在多了
    ``topics``（列：矩阵上的渠道）与 ``groups``（群组与成员）。旧 bundle 读的是
    ``data.webhooks``，那一支原样保留 —— 它只是拿不到新字段，不会报错。
    """
    matrix = await _subscription_targets(db)
    rows = await db.wecom_webhooks_all()
    last_sent = await _channel_last_sent(db)
    return {
        "success": True,
        "webhooks": [
            _safe_webhook({**row, "last_sent_at": last_sent.get(int(row["id"]), "")})
            for row in rows
        ],
        "channels": matrix["channels"],
        "topics": matrix["topics"],
        "groups": await _groups_with_members(db),
    }


@router.post("/webhooks")
async def create_webhook(
    payload: WebhookIn,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    if not payload.webhook_url:
        raise HTTPException(status_code=400, detail="webhook 地址不能为空")
    new_id = await db.wecom_webhook_create({
        "name": payload.name,
        "webhook_url_encrypted": encrypt_webhook_url(payload.webhook_url),
        "webhook_url_masked": mask_webhook_url(payload.webhook_url),
        "enabled": payload.enabled,
        "hygiene_feed": bool(payload.hygiene_feed),
        "notes": payload.notes,
    })
    if not new_id:
        raise HTTPException(status_code=500, detail="创建 webhook 失败")
    row = await db.wecom_webhook_get(new_id)
    # 旧 bundle 读 `webhook`，新页面读 `channel`（多带最近发送时间等）：两个都给，
    # 形状不变的那一支保持原样。
    return {"success": True, "webhook": _safe_webhook(row), "channel": await _channel_card(db, new_id)}


@router.put("/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int,
    payload: WebhookIn,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
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
    # 只有明确给了 true/false 才写这一列：不传（快捷开关、旧 bundle）就是"不动"。
    if payload.hygiene_feed is not None:
        update_values["hygiene_feed"] = bool(payload.hygiene_feed)
    ok = await db.wecom_webhook_update(webhook_id, update_values)
    if not ok:
        raise HTTPException(status_code=500, detail="更新 webhook 失败")
    row = await db.wecom_webhook_get(webhook_id)
    return {
        "success": True,
        "webhook": _safe_webhook(row),
        "channel": await _channel_card(db, webhook_id),
    }


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    """删除渠道。

    被推送任务引用时**拒绝**并说清是哪几条任务引用它（删掉之后那些任务就再也发不出去
    了）；被订阅引用时不拒绝 —— 订阅与群组成员是跟着渠道走的附属关系（外键级联），
    删除渠道就是取消它全部的订阅，提示里把这层说清楚。
    """
    jobs = [
        job for job in await db.wecom_jobs_all()
        if int(job.get("webhook_id") or 0) == webhook_id
    ]
    if jobs:
        names = "、".join(f"「{job.get('name') or job.get('id')}」" for job in jobs[:3])
        more = "等" if len(jobs) > 3 else ""
        raise HTTPException(
            status_code=400,
            detail=f"该渠道被推送任务 {names}{more} 引用，请先删除或改绑这些任务",
        )
    subscriptions = await db.wecom_subscriptions_all()
    drops_subscription = any(
        int(item.get("target_channel_id") or 0) == webhook_id for item in subscriptions
    )
    groups = await db.wecom_channel_groups_of_channel(webhook_id)
    group_note = ""
    if groups:
        names = "、".join(f"「{group.get('name')}」" for group in groups)
        group_note = f"；它同时会从群组 {names} 里移除"
    if drops_subscription:
        group_note += "；它的订阅会一并取消"
    ok = await db.wecom_webhook_delete(webhook_id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除 webhook 失败")
    return {"success": True, "message": f"渠道已删除{group_note}"}


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: int,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    try:
        result = await wecom_push_service.send_test_message(db, webhook_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


# ── 渠道群组与成员 ──────────────────────────────────────────────────────────


@router.get("/channel-groups")
async def list_channel_groups(db: DatabaseManager = Depends(get_db)):
    return {"success": True, "groups": await _groups_with_members(db)}


@router.post("/channel-groups")
async def create_channel_group(
    payload: ChannelGroupIn,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    group_id = await db.wecom_channel_group_create({
        "name": payload.name,
        "enabled": payload.enabled,
        "notes": payload.notes,
    })
    if not group_id:
        raise HTTPException(status_code=400, detail=_DUPLICATE_GROUP_NAME)
    return {"success": True, "group": await _group_detail(db, group_id)}


@router.put("/channel-groups/{group_id}")
async def update_channel_group(
    group_id: int,
    payload: ChannelGroupIn,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    if await db.wecom_channel_group_get(group_id) is None:
        raise HTTPException(status_code=404, detail="渠道群组不存在")
    ok = await db.wecom_channel_group_update(group_id, {
        "name": payload.name,
        "enabled": payload.enabled,
        "notes": payload.notes,
    })
    if not ok:
        raise HTTPException(status_code=500, detail="更新渠道群组失败")
    return {"success": True, "group": await _group_detail(db, group_id)}


@router.delete("/channel-groups/{group_id}")
async def delete_channel_group(
    group_id: int,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    """删除群组：成员与指向它的订阅一起走（spec 用户故事 8 的另一面 —— 保留组就是
    「停用」，那一条用 PUT 改 enabled）。"""
    if await db.wecom_channel_group_get(group_id) is None:
        raise HTTPException(status_code=404, detail="渠道群组不存在")
    ok = await db.wecom_channel_group_delete(group_id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除渠道群组失败")
    return {"success": True}


@router.post("/channel-groups/{group_id}/members")
async def add_channel_group_member(
    group_id: int,
    payload: GroupMemberIn,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    if await db.wecom_channel_group_get(group_id) is None:
        raise HTTPException(status_code=404, detail="渠道群组不存在")
    if await db.wecom_webhook_get(payload.channel_id) is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    ok = await db.wecom_channel_group_add_member(group_id, payload.channel_id)
    if not ok:
        raise HTTPException(status_code=500, detail="添加群组成员失败")
    return {"success": True, "group": await _group_detail(db, group_id)}


@router.delete("/channel-groups/{group_id}/members/{channel_id}")
async def remove_channel_group_member(
    group_id: int,
    channel_id: int,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    if await db.wecom_channel_group_get(group_id) is None:
        raise HTTPException(status_code=404, detail="渠道群组不存在")
    # 渠道已经被删掉时也要能清掉这条成员关系：先删成员再删群组是页面上的正常顺序，
    # 不应该因为外键把行带走了就报 404。
    await db.wecom_channel_group_remove_member(group_id, channel_id)
    return {"success": True, "group": await _group_detail(db, group_id)}


async def _group_detail(db: DatabaseManager, group_id: int) -> Optional[Dict[str, Any]]:
    group = await db.wecom_channel_group_get(group_id)
    if group is None:
        return None
    members = await db.wecom_channel_group_members(group_id)
    return _group_payload(group, members)


# ── 推送订阅（内容类型 × 渠道的矩阵）─────────────────────────────────────────


@router.get("/subscriptions")
async def list_subscriptions(db: DatabaseManager = Depends(get_db)):
    """订阅矩阵：``topics`` 是行（每行带 ``contains_employee_photos``），
    ``channels`` 是列。零订阅的内容类型行里 ``channels`` 为空数组 —— 页面据此高亮
    并在顶部提示，判据只有这一处。"""
    matrix = await _subscription_targets(db)
    return {
        "success": True,
        "api_version": WECOM_PUSH_API_VERSION,
        "topics": matrix["topics"],
        "channels": matrix["channels"],
    }


@router.post("/subscriptions")
async def upsert_subscription(
    payload: SubscriptionIn,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    """勾选 / 取消勾选一条订阅。

    ``enabled=False`` 是**停用**（保留那一行，重新勾上不用重配）；彻底取消走
    DELETE。同一个「内容类型 × 目标」重复勾选不会多出一行（唯一索引 + upsert）。
    """
    if payload.target_channel_id is not None:
        if await db.wecom_webhook_get(payload.target_channel_id) is None:
            raise HTTPException(status_code=404, detail="渠道不存在")
    elif await db.wecom_channel_group_get(payload.target_group_id) is None:
        raise HTTPException(status_code=404, detail="渠道群组不存在")

    try:
        subscription_id = await db.wecom_subscription_upsert({
            "topic_id": payload.topic_id,
            "target_channel_id": payload.target_channel_id,
            "target_group_id": payload.target_group_id,
            "enabled": payload.enabled,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not subscription_id:
        raise HTTPException(status_code=500, detail="保存推送订阅失败")
    return {"success": True, "subscription_id": subscription_id}


@router.delete("/subscriptions")
async def delete_subscription(
    topic_id: str = Query(..., min_length=1, max_length=60),
    target_channel_id: Optional[int] = Query(None, gt=0),
    target_group_id: Optional[int] = Query(None, gt=0),
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    if get_topic(topic_id) is None:
        raise HTTPException(status_code=400, detail=f"未知的推送内容类型: {topic_id}")
    if (target_channel_id is None) == (target_group_id is None):
        raise HTTPException(status_code=400, detail="订阅必须且只能指定一个目标（渠道或群组）")
    removed = await db.wecom_subscription_delete_for(
        topic_id,
        target_channel_id=target_channel_id,
        target_group_id=target_group_id,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="这条订阅不存在")
    return {"success": True, "removed": removed}


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
    """页面元数据：旧的字段**原样保留**（缓存着旧 bundle 的浏览器还在读它们）。

    新增的是推送内容类型注册表（票 02 / ADR 0097）：前端据此渲染内容类型下拉、
    参数表单（schema + uischema）与下拉选项，加一类内容类型只改后端注册表。

    票 06 多一个 ``api_version``：页面加载时拿它和本地构建里的版本比对，对不上显示
    顶部提示条（不阻断操作）。
    """
    return {
        "success": True,
        # 接口版本：改变本页接口形状的改动都要把它 +1（见 WECOM_PUSH_API_VERSION）。
        "api_version": WECOM_PUSH_API_VERSION,
        # ── 旧形状：`/jobs` 的 push_type 与任务模板仍按它工作，不要动 ──────────
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
        # ── 推送内容类型注册表 ────────────────────────────────────────────────
        "topics": [topic.payload() for topic in all_topics()],
        "triggers": [
            {"id": trigger.value, "name": trigger.label} for trigger in PushTrigger
        ],
    }
