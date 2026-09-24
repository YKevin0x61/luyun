#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共层：员工身份（花名册、登录、会话）。

卫生和排班都从这里取「这个人是谁」；两边谁都不认识对方的业务。
"""

from services.identity.accounts import (
    ALLOWED_PERMISSIONS,
    FORBIDDEN_SUPER_PERMISSION,
    LAST_SEEN_REFRESH_SECONDS,
    MAX_NAME_LENGTH,
    PERMISSION_ADMIN,
    PERMISSION_STAFF,
    EmployeeAccounts,
    EmployeeAccountsError,
    hash_session_id,
    normalize_phone,
    serialized_write,
)

__all__ = [
    "ALLOWED_PERMISSIONS",
    "FORBIDDEN_SUPER_PERMISSION",
    "LAST_SEEN_REFRESH_SECONDS",
    "MAX_NAME_LENGTH",
    "PERMISSION_ADMIN",
    "PERMISSION_STAFF",
    "EmployeeAccounts",
    "EmployeeAccountsError",
    "hash_session_id",
    "normalize_phone",
    "serialized_write",
]
