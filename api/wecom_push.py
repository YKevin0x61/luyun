#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业微信自动推送 API。

鉴权口径（票 06）：这一页的**写操作**只接受浏览器登录会话（``require_session``），
读操作沿用 router 级的 ``verify_admin_token``（会话 / API token / 过渡密钥都行）。
分界线是「会不会改配置」：写接口用 token 调用一律 401 「需要登录」，页面是唯一调用方，
所以没有脚本会因此断掉（spec「鉴权与审计」）。
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from api.security import require_session, verify_admin_token
from database import get_db, DatabaseManager
from services.wecom_outbox import ResolvedTarget, resolve_targets, wecom_outbox
from services.wecom_push_topics import (
    TOPIC_MANUAL_SEND,
    WECOM_PUSH_API_VERSION,
    PushTopic,
    PushTrigger,
    all_topics,
    get_topic,
    topic_display_name,
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
    """内容类型的显示名（注册表 → 内部类型 → 退回 id 本身）。

    退回 id 而不是丢掉这一行：库里可能留着某个已下线内容类型的订阅或出站行（注册表删了
    一类内容，历史还在），页面要看得见这一行才谈得上清理与排查。
    """
    return topic_display_name(topic_id)



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
    """推送任务的新形状（票 08）：内容类型 + 参数 + 时间。

    任务**不再持有收件人** —— 收件人由这个内容类型的推送订阅决定（`webhook_id` 那
    一列已经不再被读，迁移 0018 只给它补了默认值）。旧形状的请求体（带 `webhook_id`）
    由 `reject_legacy_job_payload` 明确拒绝，见那里的注释。
    """

    name: str = Field(..., min_length=1, max_length=60)
    topic_id: str = Field(..., min_length=1, max_length=60)
    # 参数形状由内容类型的 schema 决定（注册表是唯一入口）：这里原样收下对象，校验交给
    # `topic.validate_params` —— 那边的错误文案才带得上字段名（"station: 档口不存在"）。
    params: Dict[str, Any] = Field(default_factory=dict)
    schedule_time: str
    enabled: bool = True
    notes: str = Field("", max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized_name = value.strip()
        if not normalized_name:
            raise ValueError("名称不能为空")
        return normalized_name

    @field_validator("schedule_time")
    @classmethod
    def normalize_schedule_time(cls, value: str) -> str:
        return validate_schedule_time(value)


# 旧形状（页面已更新，浏览器里还缓存着旧 bundle）的字段：请求体里出现任一个，就认定
# 这是旧页面发来的。`webhook_id` 是**收件人**字段，必须明确拒绝 —— 静默忽略会让店长
# 以为改了收件人其实没改（spec「页面」一节）。
_LEGACY_JOB_FIELDS = ("webhook_id", "push_type", "date_range_mode", "station")
STALE_JOB_PAYLOAD = "页面已更新，请刷新后重试"


async def reject_legacy_job_payload(request: Request) -> None:
    """旧形状的任务写请求：400 + 明确文案，且**不产生任何写入**。

    作为依赖挂在路由上（FastAPI 先解析依赖、再校验 body 参数），所以它在 pydantic
    报「缺 topic_id」之前就拦下来 —— 店长看到的是「页面已更新，请刷新后重试」，
    而不是一串字段校验错误。
    """
    try:
        body = await request.json()
    except Exception:
        return  # 不是 JSON：交给 pydantic 去报那一条错
    if not isinstance(body, dict):
        return
    if any(field in body for field in _LEGACY_JOB_FIELDS):
        raise HTTPException(status_code=400, detail=STALE_JOB_PAYLOAD)


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
        # 渠道卡片要显示的最近一次发送成功时间（票 06/07）。群组与订阅内容在
        # /subscriptions 那一侧；「被几条任务引用」随票 08 一起下掉了 —— 任务不再绑定
        # 渠道，那个数在新模型里恒为 0。
        "last_sent_at": row.get("last_sent_at") or "",
    }


def _delivery_row(row: Dict[str, Any], channels: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """一条发送记录回给页面的形状（票 07）。

    字段就是页面表格的那七列（时间 / 目标渠道 / 内容类型 / 状态 / 字节数 / 尝试次数 /
    最后一次错误）加上 `content_summary`（鼠标悬停能认出这是哪一封）与
    `finished_at`（终态时间；没完成的行没有）。
    """
    channel_id = row.get("target_channel_id")
    channel = channels.get(int(channel_id)) if channel_id is not None else None
    topic_id = str(row.get("topic_id") or "")
    return {
        "id": int(row["id"]),
        "created_at": row.get("created_at") or "",
        "finished_at": row.get("finished_at") or "",
        "channel_id": int(channel_id) if channel_id is not None else None,
        # 渠道删掉之后名字取不到：回空串而不是让整行读不出来（历史不该跟着渠道消失）。
        "channel_name": channel.get("name", "") if channel else "",
        "topic_id": topic_id,
        # 注册表里没有这个 id（内容类型下过线）就退回 id：页面要看得见这一行。
        "topic_name": _topic_name(topic_id),
        "status": str(row.get("status") or ""),
        "message_bytes": int(row.get("message_bytes") or 0),
        "attempts": int(row.get("attempts") or 0),
        "last_error": str(row.get("last_error") or ""),
        "content_summary": str(row.get("content_summary") or ""),
    }


async def _channel_last_sent(db: DatabaseManager) -> Dict[int, str]:
    """每个渠道最近一次**成功**投递的完成时间（`{channel_id: ISO}`）。

    成功一次 = 出站表里 status = sent 的那一行，按渠道取 `MAX(finished_at)`：**聚合，
    不是采样**。以前是「取最近 200 条成功记录、按渠道挑首条」，一个长期没发过的群，
    它那条成功记录早被挤出窗口，卡片就显示成「从未发送」—— 恰恰是「这个地址是不是
    失效了」最需要的那一眼给出的却是错的（用户故事 26）。

    取不到（从未成功过）就不在字典里，页面上显示「从未发送」。
    """
    return await db.wecom_channel_last_sent()


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


async def _jobs_payload(
    db: DatabaseManager, rows: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """推送任务回给页面的形状（票 08）。

    任务只描述「内容类型 + 参数 + 时间」；卡片要显示的**订阅目标数**由服务端按订阅
    求解后给出（页面不自己推）。同一个内容类型只求解一次 —— 多条任务共用一份结果。
    """
    jobs = await db.wecom_jobs_all() if rows is None else rows
    targets_by_topic: Dict[str, List[ResolvedTarget]] = {}
    for job in jobs:
        topic_id = str(job.get("topic_id") or "")
        if topic_id and topic_id not in targets_by_topic:
            targets_by_topic[topic_id] = await resolve_targets(db, topic_id)
    return [
        _job_payload(job, targets_by_topic.get(str(job.get("topic_id") or ""), []))
        for job in jobs
    ]


def _job_payload(row: Dict[str, Any], targets: List[ResolvedTarget]) -> Dict[str, Any]:
    topic_id = str(row.get("topic_id") or "")
    try:
        params = json.loads(str(row.get("params_json") or "{}"))
    except ValueError:
        params = {}
    return {
        "id": int(row["id"]),
        "name": row.get("name", ""),
        "topic_id": topic_id,
        # 注册表里没有这个 id（内容类型下过线）就退回 id：页面要看得见这一行。
        "topic_name": _topic_name(topic_id),
        "params": params if isinstance(params, dict) else {},
        "schedule_time": row.get("schedule_time", ""),
        "enabled": bool(row.get("enabled")),
        "last_sent_date": row.get("last_sent_date", ""),
        "notes": row.get("notes", ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        # 订阅目标数：停用的渠道**也算**（订阅保留、投递跳过并在记录里写明原因）。
        "target_count": len(targets),
    }


def _job_topic(payload: JobIn) -> PushTopic:
    """任务的内容类型：必须存在，而且必须支持定时触发。"""
    topic = get_topic(payload.topic_id)
    if topic is None:
        raise HTTPException(
            status_code=400, detail=f"未知的推送内容类型: {payload.topic_id}"
        )
    if PushTrigger.SCHEDULED not in topic.triggers:
        raise HTTPException(
            status_code=400,
            detail=f"「{topic.name}」不支持定时触发，不能建成推送任务",
        )
    return topic


def _job_params(topic: PushTopic, payload: JobIn) -> Dict[str, Any]:
    """参数按注册表校验（内容类型的 schema 是唯一入口），并把顶层时间对齐进去。

    页面上只有一个时间控件（参数区里那个 time 控件，由 uischema 驱动），顶层
    `schedule_time` 是它的投影、也是调度列的值：两处必须一样，所以这里以顶层为准写回
    参数，避免「列里 21:30、参数里 09:00」这种各说各话的存档。
    """
    try:
        params = topic.validate_params(payload.params, trigger=PushTrigger.SCHEDULED)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if "schedule_time" in params:
        params["schedule_time"] = payload.schedule_time
    return params


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

    删除不拒绝：任务**不再绑定渠道**（票 08），删一个渠道不会让任何任务失效 —— 收件人
    由订阅决定。被订阅引用时不拒绝 —— 订阅与群组成员是跟着渠道走的附属关系（外键级联），
    删除渠道就是取消它全部的订阅，提示里把这层说清楚。
    """
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
    """手工把一段正文发给某个渠道（销售报表页的「推送」弹窗）。

    发出去的结果写进**统一出站**（票 07 的遗留）：它不再往旧的推送日志表里写，所以
    「手工发送」在页面的发送记录里看得见。同步发送保持不变 —— 这个入口要当场把成败
    回给用户，不排队等调度循环。
    """
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

    await wecom_outbox.record_direct_delivery(
        db,
        TOPIC_MANUAL_SEND,
        params={"text": payload.content},
        channel_id=int(webhook["id"]),
        status="sent" if status == "success" else "failed",
        message_bytes=rendered_message.byte_length,
        error=error,
    )
    return {
        "success": status == "success",
        "status": status,
        "message_bytes": rendered_message.byte_length,
        "error": error,
        "response_text": response_text,
    }


@router.get("/jobs")
async def list_jobs(db: DatabaseManager = Depends(get_db)):
    return {"success": True, "jobs": await _jobs_payload(db)}


@router.post("/jobs")
async def create_job(
    payload: JobIn,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
    _legacy: None = Depends(reject_legacy_job_payload),
):
    """新建推送任务：内容类型 + 参数 + 时间。收件人由订阅决定。"""
    topic = _job_topic(payload)
    params = _job_params(topic, payload)
    new_id = await db.wecom_job_create({
        "name": payload.name,
        "topic_id": payload.topic_id,
        "params_json": json.dumps(params, ensure_ascii=False),
        "schedule_time": payload.schedule_time,
        "enabled": payload.enabled,
        "notes": payload.notes,
    })
    if not new_id:
        raise HTTPException(status_code=500, detail="创建推送任务失败")
    job = await db.wecom_job_get(new_id)
    return {"success": True, "job": (await _jobs_payload(db, [job]))[0]}


@router.put("/jobs/{job_id}")
async def update_job(
    job_id: int,
    payload: JobIn,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
    _legacy: None = Depends(reject_legacy_job_payload),
):
    existing = await db.wecom_job_get(job_id)
    if not existing:
        raise HTTPException(status_code=404, detail="推送任务不存在")
    topic = _job_topic(payload)
    params = _job_params(topic, payload)
    ok = await db.wecom_job_update(job_id, {
        "name": payload.name,
        "topic_id": payload.topic_id,
        "params_json": json.dumps(params, ensure_ascii=False),
        "schedule_time": payload.schedule_time,
        "enabled": payload.enabled,
        "notes": payload.notes,
        # `last_sent_date` 不在写请求里：改任务不该让今天已经推过的那一次「重新可推」。
        "last_sent_date": existing.get("last_sent_date", ""),
    })
    if not ok:
        raise HTTPException(status_code=500, detail="更新推送任务失败")
    job = await db.wecom_job_get(job_id)
    return {"success": True, "job": (await _jobs_payload(db, [job]))[0]}


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: int,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    ok = await db.wecom_job_delete(job_id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除推送任务失败")
    return {"success": True}


@router.post("/jobs/{job_id}/preview")
async def preview_job(
    job_id: int,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    """预览这条任务这一刻会发出去的正文。

    它是 POST（要带任务 id 与参数，且报表类会现算数据）但**不写任何东西**；会话门禁
    与其它写路由同一口径 —— 这一页的调用方只有管理端 SPA。
    """
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
async def send_job_now(
    job_id: int,
    db: DatabaseManager = Depends(get_db),
    _session: str = Depends(require_session),
):
    """立即发送：按这个内容类型的**全部订阅目标**入队（票 08）。

    结果不再当场返回「已发送」：真正发出去由统一出站做（每渠道节流、失败退避重试），
    成败在「发送记录」里看。这里返回这次入队了几个目标与正文大小。
    """
    try:
        return await wecom_push_service.send_job_now(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/logs")
async def list_logs(
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(None, ge=1),
    topic_id: Optional[str] = Query(None, max_length=60),
    channel_id: Optional[int] = Query(None, gt=0),
    status: Optional[str] = Query(None, max_length=20),
    db: DatabaseManager = Depends(get_db),
):
    """发送记录（票 07）：读的是**统一出站表**（ADR 0095：队列表与发送记录是同一张表）。

    每行带上页面要显示的七样：时间、目标渠道、内容类型、状态、字节数、尝试次数、
    最后一次错误。渠道名与内容类型名在服务端配好（`channel_name` / `topic_name`），
    页面不必拿 id 去两张表里对照；渠道被删之后那一行的 `channel_id` 是 null、名字退回
    空串，行本身仍然读得出来（外键 ON DELETE SET NULL，历史不该跟着渠道消失）。

    ``logs`` 那一份是**旧表**的镜像，只为缓存着旧 bundle 的浏览器留着（新页面读
    `rows`）：它不是数据源，也不会再有这一页产生的新行。

    ``page_size`` 不给时退回 ``limit``：页面重构前的调用是 ``?limit=80``（没有分页与
    筛选），那一次它要的就是「最近 80 条」—— 切表之后这个口径逐字保住。页大小上限
    200，一次请求拉不走整张表。
    """
    size = min(page_size or limit, 200)
    result = await db.wecom_outbox_page(
        page=page,
        page_size=size,
        topic_id=topic_id,
        channel_id=channel_id,
        status=status,
    )
    channels = {int(row["id"]): row for row in await db.wecom_webhooks_all()}
    return {
        "success": True,
        "api_version": WECOM_PUSH_API_VERSION,
        "rows": [_delivery_row(row, channels) for row in result["rows"]],
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "pages": result["pages"],
        # 旧形状：只读镜像，见 docstring。
        "logs": await db.wecom_logs_recent(limit),
    }


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
