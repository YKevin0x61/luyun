#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
登录凭据存储

使用 Fernet 对称加密把 POS 系统的账号、密码、门店 ID 等信息持久化到
``data/credentials.enc``。加密密钥优先读取环境变量 ``LUYUN_CRED_KEY``，
否则自动生成并保存到 ``data/.cred_key``（仅本机可读）。

设计目标：
- 任何敏感信息都不再以明文形式留在仓库中。
- 与现有的 ``config/config.json`` 保留一次性向后兼容：
  若发现旧文件含 ``login.username/password``，启动时自动迁移并把旧
  文件中的敏感字段擦除。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

CHINA_TZ = timezone(timedelta(hours=8))

# —— 备份/迁移（口令加密整包）——
BACKUP_VERSION = 1
BACKUP_KDF_ITERATIONS = 200_000
BACKUP_SALT_BYTES = 16
BACKUP_PASSPHRASE_MIN_LENGTH = 6

# 文件位置基于本文件，不受 cwd 影响
_BASE_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_CRED_FILE = _DATA_DIR / "credentials.enc"
_KEY_FILE = _DATA_DIR / ".cred_key"
_LEGACY_CONFIG_CANDIDATES = (
    _BASE_DIR / "config" / "config.json",
)


def _find_legacy_config_file() -> Optional[Path]:
    for path in _LEGACY_CONFIG_CANDIDATES:
        if path.exists():
            return path
    return None

_FILE_MODE = 0o600

# 默认 cy7mm API 根路径（业务 API 仍走 cy7mm Cookie，登录已迁移至龙管家 2.0）
DEFAULT_API_HOST = "https://cy7mm.wuuxiang.com"

_lock = threading.RLock()
_cache: Optional["CredentialBundle"] = None


# ==================== 数据模型 ====================

@dataclass
class CredentialBundle:
    """运行时使用的凭据实体。

    字段约定（POS 系统命名混乱，这里统一文档化）：
    - ``shop_id``: tableList URL 第一个 ID（POS 后端实际为 centerId）
    - ``company_id``: tableList URL 第二个 ID（POS 后端为 shopId）
    - ``delivery_shop_id``: 已结账单接口 ``shops`` 与 ``shopId`` 字段使用的 ID
    """

    phone: str
    password: str
    shop_id: str
    company_id: str
    shop_name: str
    delivery_shop_id: str
    updated_at: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def table_list_url(self) -> str:
        """爬虫导航用的餐桌列表页 URL（含 shopName 中文参数）。"""
        from urllib.parse import quote
        return (
            f"{DEFAULT_API_HOST}/home/tableList/1/{self.shop_id}/{self.company_id}"
            f"?shopName={quote(self.shop_name)}"
        )

    @property
    def table_list_referer(self) -> str:
        """API 调用 ``Referer`` 头使用的 URL（与浏览器一致）。"""
        return self.table_list_url

    @property
    def occupy_table_referer_template(self) -> str:
        """``getbsdetail`` 接口 ``Referer`` 头使用的 URL 模板，包含 ``{point_id}`` 占位。"""
        return f"{DEFAULT_API_HOST}/home/occupyTable/{{point_id}}/{self.shop_id}/{self.company_id}"

    @property
    def closed_tables_referer(self) -> str:
        """已结账单页的 ``Referer``。"""
        return f"{DEFAULT_API_HOST}/home/colsedTableSeats/1/{self.shop_id}/{self.company_id}"

    def to_storage(self) -> dict:
        return asdict(self)

    def to_safe_dict(self) -> dict:
        """脱敏后的展示数据（用于 API 返回）。"""
        return {
            "phone": _mask_phone(self.phone),
            "phone_raw_length": len(self.phone or ""),
            "password_set": bool(self.password),
            "shop_id": self.shop_id,
            "company_id": self.company_id,
            "shop_name": self.shop_name,
            "delivery_shop_id": self.delivery_shop_id,
            "updated_at": self.updated_at,
        }


# ==================== 加密 / 持久化 ====================

def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_or_create_key() -> bytes:
    """读取或创建 Fernet 密钥。

    优先级：``LUYUN_CRED_KEY`` 环境变量 > ``data/.cred_key`` 本地文件。
    """
    env_key = os.environ.get("LUYUN_CRED_KEY")
    if env_key:
        try:
            return env_key.strip().encode("ascii")
        except Exception as exc:
            logger.warning("环境变量 LUYUN_CRED_KEY 不是有效 Fernet 密钥: %s", exc)

    _ensure_data_dir()
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()

    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    try:
        os.chmod(_KEY_FILE, _FILE_MODE)
    except OSError:
        pass
    logger.warning(
        "🔑 已自动生成凭据加密密钥 %s，请妥善备份。要覆盖请在环境变量 LUYUN_CRED_KEY 中提供 base64 Fernet key。",
        _KEY_FILE,
    )
    return key


def _fernet() -> Fernet:
    return Fernet(_load_or_create_key())


def _write_encrypted(payload: dict) -> None:
    _ensure_data_dir()
    token = _fernet().encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    _CRED_FILE.write_bytes(token)
    try:
        os.chmod(_CRED_FILE, _FILE_MODE)
    except OSError:
        pass


def _read_encrypted() -> Optional[dict]:
    if not _CRED_FILE.exists():
        return None
    try:
        token = _CRED_FILE.read_bytes()
        if not token:
            return None
        raw = _fernet().decrypt(token)
        return json.loads(raw.decode("utf-8"))
    except InvalidToken:
        logger.error(
            "❌ 凭据文件 %s 解密失败，可能是密钥不匹配（环境变量 LUYUN_CRED_KEY 与 %s 不一致）",
            _CRED_FILE,
            _KEY_FILE,
        )
        return None
    except Exception as exc:
        logger.error("❌ 读取凭据文件失败: %s", exc)
        return None


# ==================== 工具方法 ====================

def _mask_phone(phone: str) -> str:
    if not phone:
        return ""
    if len(phone) <= 4:
        return "*" * len(phone)
    return f"{phone[:3]}****{phone[-4:]}"


def _looks_like_base64_encoded_password(value: str) -> bool:
    """判断字符串是否疑似 base64 编码后的密码。

    判断条件：长度可被 4 整除、仅包含 base64 字符、解码后结果是可打印 ASCII。
    """
    if not value or len(value) % 4 != 0:
        return False
    if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/=" for c in value):
        return False
    try:
        decoded = base64.b64decode(value).decode("utf-8")
    except Exception:
        return False
    return all(32 <= ord(c) < 127 for c in decoded)


def _normalize_password(value: str) -> str:
    """如果传入的是疑似 base64 编码后的密码，自动解码为明文。

    向后兼容旧版 ``config/config.json`` 中的 ``eUFOcUlORzUwMzAyOTE0OA==``。
    """
    if _looks_like_base64_encoded_password(value):
        try:
            return base64.b64decode(value).decode("utf-8")
        except Exception:
            return value
    return value


# ==================== 公共 API ====================

def get_credentials() -> Optional[CredentialBundle]:
    """获取当前缓存的凭据。"""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        data = _read_encrypted()
        if not data:
            return None
        try:
            _cache = _bundle_from_storage(data)
            return _cache
        except Exception as exc:
            logger.error("❌ 凭据反序列化失败: %s", exc)
            return None


def save_credentials(payload: dict) -> CredentialBundle:
    """保存新凭据。``payload`` 至少需要 phone + password + shop_id + company_id + shop_name。"""
    global _cache
    bundle = _bundle_from_payload(payload)
    with _lock:
        _write_encrypted(bundle.to_storage())
        _cache = bundle
        logger.info(
            "🔐 已更新登录凭据（账号 %s, shopId=%s, companyId=%s, shopName=%s）",
            _mask_phone(bundle.phone),
            bundle.shop_id,
            bundle.company_id,
            bundle.shop_name,
        )
    return bundle


def clear_credentials() -> bool:
    """清空保存的凭据。"""
    global _cache
    with _lock:
        existed = _CRED_FILE.exists()
        if existed:
            try:
                _CRED_FILE.unlink()
            except OSError as exc:
                logger.error("❌ 删除凭据文件失败: %s", exc)
                return False
        _cache = None
        logger.info("🧹 登录凭据已清空")
        return existed


def has_credentials() -> bool:
    return get_credentials() is not None


# ==================== 备份 / 迁移（口令加密整包）====================

def _derive_backup_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    """用 PBKDF2-HMAC-SHA256 从口令派生 32 字节 Fernet 密钥（urlsafe base64）。"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    derived = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


def reload() -> Optional[CredentialBundle]:
    """强制刷新缓存（在 API 写入后让 scraper 立刻拿到新值）。"""
    global _cache
    with _lock:
        _cache = None
    return get_credentials()


# ==================== 内部转换 ====================

def _bundle_from_storage(data: dict) -> CredentialBundle:
    return CredentialBundle(
        phone=str(data.get("phone", "")),
        password=str(data.get("password", "")),
        shop_id=str(data.get("shop_id", "")),
        company_id=str(data.get("company_id", "")),
        shop_name=str(data.get("shop_name", "")),
        delivery_shop_id=str(data.get("delivery_shop_id") or data.get("company_id") or ""),
        updated_at=data.get("updated_at"),
        extra=dict(data.get("extra") or {}),
    )


def _bundle_from_payload(payload: dict) -> CredentialBundle:
    required = ("phone", "password", "shop_id", "company_id", "shop_name")
    missing = [k for k in required if not str(payload.get(k) or "").strip()]
    if missing:
        raise ValueError(f"缺少必填字段: {', '.join(missing)}")

    phone = str(payload["phone"]).strip()
    password = _normalize_password(str(payload["password"]))
    shop_id = str(payload["shop_id"]).strip()
    company_id = str(payload["company_id"]).strip()
    shop_name = str(payload["shop_name"]).strip()
    delivery_shop_id = str(payload.get("delivery_shop_id") or company_id).strip()

    if not phone.isdigit():
        raise ValueError("账号必须是手机号（纯数字）")
    if not shop_id.isdigit() or not company_id.isdigit() or not delivery_shop_id.isdigit():
        raise ValueError("shop_id / company_id / delivery_shop_id 必须为纯数字")

    return CredentialBundle(
        phone=phone,
        password=password,
        shop_id=shop_id,
        company_id=company_id,
        shop_name=shop_name,
        delivery_shop_id=delivery_shop_id,
        updated_at=datetime.now(CHINA_TZ).isoformat(),
        extra=dict(payload.get("extra") or {}),
    )


# ==================== 一次性迁移 ====================

def migrate_legacy_config() -> Optional[CredentialBundle]:
    """从旧版 ``config/config.json`` 迁移凭据到加密文件，并擦除旧文件中的敏感字段。

    仅在加密文件不存在且旧文件有完整登录信息时执行。
    """
    if _CRED_FILE.exists():
        return None
    legacy_path = _find_legacy_config_file()
    if legacy_path is None:
        return None

    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("⚠️ 旧 config.json 解析失败，跳过迁移: %s", exc)
        return None

    login = legacy.get("login") or {}
    phone = (login.get("username") or login.get("phone") or "").strip()
    raw_password = (login.get("password") or "").strip()
    if not phone or not raw_password:
        return None

    target_url = (legacy.get("urls") or {}).get("target_url") or ""
    shop_id, company_id, shop_name = _parse_target_url(target_url)
    if not shop_id or not company_id:
        logger.warning("⚠️ 无法从 target_url 解析门店 ID，请稍后在 /setup 页面手工填写")
        return None

    bundle = CredentialBundle(
        phone=phone,
        password=_normalize_password(raw_password),
        shop_id=shop_id,
        company_id=company_id,
        shop_name=shop_name or "",
        delivery_shop_id=company_id,
        updated_at=datetime.now(CHINA_TZ).isoformat(),
        extra={"migrated_from": str(legacy_path.relative_to(_BASE_DIR))},
    )

    with _lock:
        _write_encrypted(bundle.to_storage())
        _cache_set(bundle)

    _scrub_legacy_config(legacy, legacy_path)
    logger.warning(
        "🔁 已从 %s 迁移登录凭据并擦除旧文件中的敏感字段（账号 %s）",
        legacy_path.relative_to(_BASE_DIR),
        _mask_phone(phone),
    )
    return bundle


def _parse_target_url(url: str) -> tuple[str, str, str]:
    """从形如 ``/home/tableList/1/{shop_id}/{company_id}?shopName=...`` 解析三元组。"""
    if not url:
        return "", "", ""
    try:
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        shop_id = ""
        company_id = ""
        if "tableList" in parts:
            i = parts.index("tableList")
            if len(parts) >= i + 4:
                shop_id = parts[i + 2]
                company_id = parts[i + 3]
        qs = parse_qs(parsed.query)
        shop_name = unquote((qs.get("shopName") or [""])[0])
        return shop_id, company_id, shop_name
    except Exception:
        return "", "", ""


def _scrub_legacy_config(legacy: dict, legacy_path: Path) -> None:
    """把旧 config.json 里的 login 字段清空，但保留其他配置以免破坏现有功能。"""
    try:
        login = legacy.get("login") or {}
        login["username"] = ""
        login["password"] = ""
        login["auto_login"] = login.get("auto_login", True)
        legacy["login"] = login
        legacy_path.write_text(
            json.dumps(legacy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(legacy_path, _FILE_MODE)
        except OSError:
            pass
    except Exception as exc:
        logger.warning("⚠️ 擦除旧 config.json 敏感字段失败: %s", exc)


def _cache_set(bundle: CredentialBundle) -> None:
    global _cache
    _cache = bundle
