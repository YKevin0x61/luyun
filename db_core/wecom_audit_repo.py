#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DatabaseManager 的推送配置审计职责：``wecom_push_audit`` 的写入与分页查询。

「企业微信推送」页上的每一次配置写操作（渠道 / 群组 / 成员 / 订阅 / 任务）都往这张
表追加一行，页面第五个 tab「变更历史」读它（票 11，spec「鉴权与审计」）。

表由迁移 ``0019_wecom_push_audit.sql`` 建立。两条只属于这张表的约定：

* **只追加**：没有 UPDATE、没有 DELETE，也没有保留期清理 —— 它不该跟着发送记录的
  90 天保留策略走（配置变更的价值恰恰在半年后回看）。
* **失败只记日志**（与 ``wecom_repo.py`` / ``wecom_subscriptions_repo.py`` 的既有
  方法一致，返回空值而不抛）：审计写失败**不能**让原本成功的配置变更报错。
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)

# 「变更历史」tab 的页码与页大小。与发送记录同一个口径（一页 50、上限 200）：
# 两个表格的翻页手感要一致，而上限挡的是「一次请求把整张审计表拉下来」。
AUDIT_PAGE_SIZE_DEFAULT = 50
AUDIT_PAGE_SIZE_MAX = 200


def _now() -> str:
    return datetime.now(CHINA_TZ).isoformat()


class _WecomAuditRepoMixin:
    """推送配置变更历史的追加与分页读取。"""

    async def wecom_audit_add(self, item: Dict[str, Any]) -> int:
        """追加一条变更记录，返回它的 id。

        只做 INSERT：审计记录写进去就不再改（改历史等于没有历史）。
        ``before_json`` / ``after_json`` 由调用方序列化好（服务层
        ``services/wecom_audit.py`` 的 ``record()``），这里不解释它们的内容。
        """
        try:
            tdb = self._connection.table("wecom_push_audit")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO wecom_push_audit
                       (actor, action, object_type, object_id, object_name,
                        before_json, after_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(item.get("actor") or ""),
                        str(item["action"]),
                        str(item["object_type"]),
                        item.get("object_id"),
                        str(item.get("object_name") or ""),
                        str(item.get("before_json") or "{}"),
                        str(item.get("after_json") or "{}"),
                        item.get("created_at") or _now(),
                    ),
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return int(row_id or 0)
        except Exception as e:
            # 审计写失败只到这里为止：调用点不检查返回值，业务事务已经提交过了。
            logger.error(f"❌ 写入推送配置变更记录失败: {e}")
            return 0

    async def wecom_audit_page(
        self,
        page: int = 1,
        page_size: int = AUDIT_PAGE_SIZE_DEFAULT,
        object_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按时间倒序取一页变更记录（可按对象类型筛），返回行 + 总数 + 页数。

        倒序是**页面的默认读法**（最近发生了什么），所以排序键与索引
        ``idx_wecom_push_audit_created`` 一致：``created_at DESC, id DESC``。
        秒级时间戳会撞（一次保存连写好几条），第二键用 id 才谈得上稳定分页。
        """
        size = max(1, min(int(page_size or AUDIT_PAGE_SIZE_DEFAULT), AUDIT_PAGE_SIZE_MAX))
        current = max(1, int(page or 1))
        try:
            tdb = self._connection.table("wecom_push_audit")
            conditions: List[str] = []
            params: List[Any] = []
            if object_type:
                conditions.append("object_type = ?")
                params.append(str(object_type))
            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

            async with tdb.conn.cursor() as cursor:
                await cursor.execute(f"SELECT COUNT(*) AS total FROM wecom_push_audit{where}", params)
                total_row = await cursor.fetchone()
                total = int(dict(total_row)["total"]) if total_row else 0

                await cursor.execute(
                    f"""SELECT id, created_at, actor, action, object_type, object_id,
                               object_name, before_json, after_json
                        FROM wecom_push_audit{where}
                        ORDER BY created_at DESC, id DESC
                        LIMIT ? OFFSET ?""",
                    [*params, size, (current - 1) * size],
                )
                rows = await cursor.fetchall()
            return {
                "rows": [dict(row) for row in rows],
                "total": total,
                "page": current,
                "page_size": size,
                "pages": (total + size - 1) // size,
            }
        except Exception as e:
            logger.error(f"❌ 获取推送配置变更记录失败: {e}")
            return {"rows": [], "total": 0, "page": current, "page_size": size, "pages": 0}
