#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的应用运行配置（app_settings 键值表）职责。

存放非敏感的运行期配置（营业时段、轮询间隔、浏览器选项等），
以 ``key -> JSON 字符串`` 的形式单行存取。敏感凭据仍走
``services/credentials_store.py`` 的加密文件，二者不混用。
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)


class _SettingsRepoMixin:
    """app_settings 键值表的 JSON 读写。"""

    async def settings_get_json(self, key: str, default: Any = None) -> Any:
        """读取某个配置键的 JSON 值，不存在或解析失败时返回 ``default``。"""
        try:
            tdb = self.table("app_settings")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT value FROM app_settings WHERE key = ?", (key,)
                )
                row = await cursor.fetchone()
            if not row or row[0] is None:
                return default
            return json.loads(row[0])
        except json.JSONDecodeError as exc:
            logger.error("❌ 配置项 %s JSON 解析失败: %s", key, exc)
            return default
        except Exception as exc:
            logger.error("❌ 读取配置项 %s 失败: %s", key, exc)
            return default

    async def settings_set_json(self, key: str, value: Any) -> bool:
        """以 JSON 形式写入（upsert）某个配置键。"""
        try:
            payload = json.dumps(value, ensure_ascii=False)
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("app_settings")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, payload, now),
                )
            await tdb.commit()
            return True
        except Exception as exc:
            logger.error("❌ 写入配置项 %s 失败: %s", key, exc)
            return False

    async def settings_updated_at(self, key: str) -> Optional[str]:
        """返回某配置键的最后更新时间，不存在时返回 None。"""
        try:
            tdb = self.table("app_settings")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT updated_at FROM app_settings WHERE key = ?", (key,)
                )
                row = await cursor.fetchone()
            return row[0] if row else None
        except Exception as exc:
            logger.error("❌ 读取配置项 %s 更新时间失败: %s", key, exc)
            return None
