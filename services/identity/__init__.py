#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共层：员工身份（花名册、登录、会话）+ 工作区名单 + 「今天谁在哪」。

卫生和排班都从这里取「这个人是谁」「有哪些区」「今天排到哪个班哪个区」；
两边谁都不认识对方的业务。
"""

from services.identity.accounts import (
    ALLOWED_PERMISSIONS,
    FOREVER_EXPIRES_AT,
    FORBIDDEN_SUPER_PERMISSION,
    LAST_SEEN_REFRESH_SECONDS,
    MAX_NAME_LENGTH,
    PERSISTENT_SESSION_COOKIE_SECONDS,
    PERMISSION_ADMIN,
    PERMISSION_STAFF,
    EmployeeAccounts,
    EmployeeAccountsError,
    hash_session_id,
    is_forever_expiry,
    normalize_phone,
    remember_session_forever,
    serialized_write,
)

from services.identity.duty import DutyRoster
from services.identity.zones import ZoneDirectory

__all__ = [
    "ALLOWED_PERMISSIONS",
    "FOREVER_EXPIRES_AT",
    "FORBIDDEN_SUPER_PERMISSION",
    "LAST_SEEN_REFRESH_SECONDS",
    "MAX_NAME_LENGTH",
    "PERSISTENT_SESSION_COOKIE_SECONDS",
    "PERMISSION_ADMIN",
    "PERMISSION_STAFF",
    "DutyRoster",
    "EmployeeAccounts",
    "EmployeeAccountsError",
    "ZoneDirectory",
    "hash_session_id",
    "is_forever_expiry",
    "normalize_phone",
    "remember_session_forever",
    "serialized_write",
]
