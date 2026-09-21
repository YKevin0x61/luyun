#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份点：统一清单、两层校验、保留清理、恢复编排与备份健康。

这是备份服务的对外结论层：管理后台、更新作业适配器与冷备入口都经由它读写、
判断备份点。所有结论都只读（除显式清理与恢复动作），且用外部可观察的行为表达：
给定一台有某些数据与照片的店面、给定某份备份点，这里回答「它能不能恢复」「这次
恢复会生效哪些部分」。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from config import settings
from services import backup_retention, backup_service
from services.backup_service import (
    CONTENT_APP_DB,
    CONTENT_APP_PG,
    CONTENT_CREDENTIALS,
    CONTENT_LABELS,
    CONTENT_OTHER_PHOTOS,
    CONTENT_RECIPES,
    CONTENT_RUNTIME,
    CONTENT_STANDARD_PHOTOS,
    KEY_TABLES,
    PHOTO_OTHER,
    PHOTO_STANDARD,
    PROVENANCE_LABELS,
    PROVENANCE_MANUAL,
)
from services.credentials_store import CHINA_TZ

logger = logging.getLogger(__name__)

MEDIUM_SNAPSHOT = "local_snapshot"
MEDIUM_EXPORT = "export_backup"
MEDIUM_COLD = "cold_backup"

MEDIUM_LABELS = {
    MEDIUM_SNAPSHOT: "本机回滚快照",
    MEDIUM_EXPORT: "导出备份",
    MEDIUM_COLD: "冷备",
}
MEDIUM_PURPOSES = {
    MEDIUM_SNAPSHOT: "快速回滚",
    MEDIUM_EXPORT: "离机保存",
    MEDIUM_COLD: "宿主机定时产出",
}

# 明确不纳入备份的内容与理由（页面就近展示）
NOT_BACKED_UP = [
    {"name": "日志库", "reason": "写入量大且非业务数据，回滚它没有意义"},
    {"name": "采集状态文件", "reason": "随运行过程自愈，恢复旧值反而会漏采或重采"},
    {"name": "更新访问凭据", "reason": "敏感材料留在受控位置，不随备份外流"},
    {"name": "更新作业状态", "reason": "数据回滚后不应与旧版本历史互相矛盾"},
]

PHOTO_CONTENT_BY_KIND = {
    PHOTO_STANDARD: CONTENT_STANDARD_PHOTOS,
    PHOTO_OTHER: CONTENT_OTHER_PHOTOS,
}

_health_cache: Optional[dict] = None


# ==================== 备份点清单 ====================

def _now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat()


def _merge_in_backup_consistency(basic: dict, consistency: Optional[dict]) -> dict:
    """把备份点的照片完整度结论并进基础校验。

    只有 ``blocking`` 的结论才判负（那才是「这份备份自身坏了」）；照片在源磁盘上
    本来就缺失这类完整度问题降级成 ``warnings``，不拦下数据回滚。
    """
    if not consistency:
        return basic
    if consistency.get("blocking") and not consistency.get("ok", True):
        messages = list(basic.get("messages") or [])
        messages.extend(consistency.get("errors") or [])
        return {"ok": False, "messages": messages}
    if consistency.get("ok", True):
        return basic
    merged = dict(basic)
    merged["warnings"] = list(basic.get("warnings") or []) + list(
        consistency.get("errors") or []
    )
    return merged


def _snapshot_points() -> List[dict]:
    points: List[dict] = []
    for item in backup_service.list_snapshots():
        contents = list(item.get("contents") or [])
        consistency = item.get("consistency")
        point = {
            "id": f"snapshot:{item['ts']}",
            "medium": MEDIUM_SNAPSHOT,
            "medium_label": MEDIUM_LABELS[MEDIUM_SNAPSHOT],
            "purpose": MEDIUM_PURPOSES[MEDIUM_SNAPSHOT],
            "location": str(backup_service._snapshot_root() / item["ts"]),
            "created_at": item["created_at"],
            "provenance": item.get("provenance") or PROVENANCE_MANUAL,
            "provenance_label": PROVENANCE_LABELS.get(
                item.get("provenance") or PROVENANCE_MANUAL, "手动"
            ),
            "size_bytes": item["size_bytes"],
            "contents": contents,
            "contents_labels": [CONTENT_LABELS.get(c, c) for c in contents],
            "photos": item.get("photos") or {},
            "consistency": consistency,
            "restorable": True,
            "detail": {"ts": item["ts"], "files": item.get("files") or []},
        }
        point["missing"] = _missing_from_contents(contents)
        point["basic_check"] = _merge_in_backup_consistency(
            _snapshot_basic_check(item), consistency
        )
        point["recoverable"] = bool(point["basic_check"]["ok"])
        points.append(point)
    return points


def _export_points() -> List[dict]:
    points: List[dict] = []
    for item in backup_service.list_export_archives():
        meta = item.get("meta") or {}
        includes = meta.get("includes") or {}
        contents = _contents_from_includes(includes)
        point = {
            "id": f"export:{item['name']}",
            "medium": MEDIUM_EXPORT,
            "medium_label": MEDIUM_LABELS[MEDIUM_EXPORT],
            "purpose": MEDIUM_PURPOSES[MEDIUM_EXPORT],
            "location": item["path"],
            "created_at": item["created_at"],
            "provenance": item.get("provenance") or PROVENANCE_MANUAL,
            "provenance_label": PROVENANCE_LABELS.get(
                item.get("provenance") or PROVENANCE_MANUAL, "手动"
            ),
            "size_bytes": item["size_bytes"],
            "contents": contents,
            "contents_labels": [CONTENT_LABELS.get(c, c) for c in contents],
            "photos": meta.get("photos") or {},
            "consistency": meta.get("consistency"),
            "restorable": True,
            "detail": {"name": item["name"], "has_sidecar": item.get("has_sidecar")},
        }
        point["missing"] = _missing_from_contents(contents)
        point["basic_check"] = _merge_in_backup_consistency(
            _export_basic_check(item), meta.get("consistency")
        )
        point["recoverable"] = bool(point["basic_check"]["ok"])
        points.append(point)
    return points


def _cold_scan_point(item: dict) -> dict:
    """从目录扫描得到的一条冷备备份点（状态文件缺失或该次运行未被报告）。"""
    contents = list(item.get("contents") or [CONTENT_APP_DB])
    point = {
        "id": f"cold:{item['ts']}",
        "medium": MEDIUM_COLD,
        "medium_label": MEDIUM_LABELS[MEDIUM_COLD],
        "purpose": MEDIUM_PURPOSES[MEDIUM_COLD],
        "location": item["archive"],
        "created_at": None,
        "provenance": PROVENANCE_MANUAL,
        "provenance_label": "冷备目录（任务未报告）",
        "size_bytes": item.get("size_bytes") or 0,
        "contents": contents,
        "contents_labels": [CONTENT_LABELS.get(c, c) for c in contents],
        "photos": {},
        "consistency": None,
        "restorable": False,
        "detail": {
            "cold_scan": item,
            "read_only": True,
            "reported": False,
        },
    }
    point["missing"] = _missing_from_contents(contents)
    point["basic_check"] = _cold_scan_basic_check(item)
    point["recoverable"] = bool(point["basic_check"]["ok"])
    return point


def _cold_points() -> List[dict]:
    """宿主机冷备：以冷备任务写出的状态文件为主，目录扫描兜底。

    状态文件如实表达「跑没跑」与「跑成了没」：失败运行也是一条备份点（不可恢复），
    不会因为归档不存在就退化成「任务未报告」。旧于本次运行的归档仍然一并列出。
    """
    status = backup_service.read_cold_backup_status()
    if not status:
        # 状态文件缺失（旧部署）→ 目录扫描兜底
        return [_cold_scan_point(item) for item in backup_service.scan_cold_backup_dirs()]

    points: List[dict] = []
    ts = status.get("ts") or "latest"
    archive = Path(status.get("archive") or "")
    contents = list(status.get("contents") or [])
    point = {
        "id": f"cold:{ts}",
        "medium": MEDIUM_COLD,
        "medium_label": MEDIUM_LABELS[MEDIUM_COLD],
        "purpose": MEDIUM_PURPOSES[MEDIUM_COLD],
        "location": str(archive) if status.get("archive") else None,
        "created_at": status.get("ran_at"),
        "provenance": PROVENANCE_MANUAL,
        "provenance_label": "冷备任务",
        "size_bytes": status.get("size_bytes") or 0,
        "contents": contents,
        "contents_labels": [CONTENT_LABELS.get(c, c) for c in contents],
        "photos": {},
        "consistency": status.get("consistency"),
        "restorable": False,
        "detail": {"cold_status": status, "read_only": True, "reported": True},
    }
    if status.get("ok") and archive.is_file():
        basic = _cold_status_basic_check(status)
    elif status.get("ok"):
        basic = {"ok": False, "messages": ["状态文件报告成功，但冷备归档已不存在"]}
    else:
        basic = {
            "ok": False,
            "messages": [
                f"最近一次冷备任务失败：{status.get('error') or '未知原因'}"
            ],
        }
    point["missing"] = _missing_from_contents(contents)
    point["basic_check"] = _merge_in_backup_consistency(basic, status.get("consistency"))
    point["recoverable"] = bool(point["basic_check"]["ok"])
    points.append(point)

    # 本次运行之外的旧归档仍然可见（含状态文件未覆盖的历史目录）
    for item in backup_service.scan_cold_backup_dirs():
        if item["ts"] == ts:
            continue
        points.append(_cold_scan_point(item))
    return points


def list_backup_points() -> List[dict]:
    """三种介质收拢成一份备份点列表（时间倒序）。"""
    points = _snapshot_points() + _export_points() + _cold_points()
    points.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
    return points


def get_backup_point(point_id: str) -> Optional[dict]:
    for point in list_backup_points():
        if point["id"] == point_id:
            return point
    return None


def _contents_from_includes(includes: Dict[str, Any]) -> List[str]:
    mapping = (
        (CONTENT_RUNTIME, "runtime"),
        (CONTENT_APP_DB, "app_db"),
        (CONTENT_APP_PG, "app_pg"),
        (CONTENT_RECIPES, "recipes_db"),
        (CONTENT_STANDARD_PHOTOS, "standard_photos"),
        (CONTENT_OTHER_PHOTOS, "other_photos"),
    )
    contents = [content for content, key in mapping if includes.get(key)]
    # 运行配置（营业时段 / 轮询间隔等）落在 app_settings 表里，两种后端的整库
    # 副本都会带上它，因此只要带了业务数据，运行配置就随之一并恢复。
    if (
        CONTENT_APP_DB in contents or CONTENT_APP_PG in contents
    ) and CONTENT_RUNTIME not in contents:
        contents.append(CONTENT_RUNTIME)
    return contents


def missing_contents(contents: Sequence[str]) -> List[dict]:
    """缺项摘要：这份备份点缺了哪些内容类别（公开给 API 层复用）。

    走 ``contents_cover`` 而不是直接比较：PG 的整库 pg_dump（``app_pg``）同时
    覆盖业务数据与配方数据，直接比较会把一份完好的 PG 快照报成两项缺失。
    """
    missing = []
    for content in (
        CONTENT_CREDENTIALS,
        CONTENT_RUNTIME,
        CONTENT_APP_DB,
        CONTENT_RECIPES,
        CONTENT_STANDARD_PHOTOS,
        CONTENT_OTHER_PHOTOS,
    ):
        if not backup_service.contents_cover(contents, content):
            missing.append({"content": content, "label": CONTENT_LABELS[content]})
    return missing


# 兼容内部旧名
_missing_from_contents = missing_contents


# ==================== 基础校验（只读）====================

def _open_backup_sqlite(db_path: Path) -> sqlite3.Connection:
    """以真正只读的方式打开备份内的库（不产生 -shm/-wal，不修改备份点）。

    备份点里的库由 SQLite backup API 生成，是自洽单文件；``immutable=1`` 因此既
    安全又不会就地写入。若文件仍被判定不可读，退回复制到临时目录再打开。
    """
    uri = db_path.resolve().as_uri() + "?immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn
    except sqlite3.Error:
        try:
            conn.close()
        except Exception:
            pass

    import tempfile

    tmp_dir = tempfile.mkdtemp(prefix="luyun-validate-")
    tmp_path = Path(tmp_dir) / "app.db"
    shutil.copy2(db_path, tmp_path)
    conn = sqlite3.connect(f"{tmp_path.resolve().as_uri()}?immutable=1", uri=True)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _sqlite_basic_check(db_path: Path, *, expected_rows: Optional[dict] = None) -> dict:
    messages: List[str] = []
    if not db_path.is_file():
        return {"ok": False, "messages": ["备份内没有业务数据库"]}
    if db_path.stat().st_size <= 0:
        return {"ok": False, "messages": ["业务数据库体积为零"]}
    try:
        conn = _open_backup_sqlite(db_path)
    except sqlite3.Error as exc:
        return {"ok": False, "messages": [f"业务数据库无法打开：{exc}"]}
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    except sqlite3.Error as exc:
        conn.close()
        return {"ok": False, "messages": [f"业务数据库无法读取：{exc}"]}
    try:
        if "orders" not in names:
            return {"ok": False, "messages": ["缺少关键表 orders，备份不可恢复"]}
        for table in KEY_TABLES:
            if table not in names:
                messages.append(f"关键表 {table} 不存在（旧备份）")
                continue
            try:
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            except sqlite3.Error as exc:
                return {"ok": False, "messages": [f"关键表 {table} 不可读：{exc}"]}
        if expected_rows:
            mismatches = backup_service.row_count_mismatches(
                expected_rows, backup_service._count_rows(conn)
            )
            if mismatches:
                return {"ok": False, "messages": mismatches}
    finally:
        conn.close()
    return {"ok": True, "messages": messages}


def _snapshot_basic_check(item: dict) -> dict:
    snap_dir = backup_service._snapshot_root() / item["ts"]
    if item["size_bytes"] <= 0:
        return {"ok": False, "messages": ["备份点体积为零"]}
    db_path = snap_dir / "app.db"
    if db_path.is_file():
        return _sqlite_basic_check(db_path, expected_rows=item.get("row_counts") or {})
    # PG 后端的业务数据是整库 pg_dump，没有 app.db。不认这个文件会把它误判成
    # 「仅含凭据」，与「内容：业务数据 (PostgreSQL)」自相矛盾。
    pg_dump_path = snap_dir / "app.pgdump"
    if pg_dump_path.is_file():
        if pg_dump_path.stat().st_size <= 0:
            return {"ok": False, "messages": ["PostgreSQL 整库备份体积为零"]}
        return {
            "ok": True,
            "messages": [
                "业务数据是 PostgreSQL 整库备份（app.pgdump）：页面内「恢复整库数据」"
                "用 pg_restore --clean 重建数据库对象，恢复期间采集会中断一轮，"
                "完成后需要重新登录后台"
            ],
        }
    if (snap_dir / "credentials.enc").is_file():
        return {"ok": True, "messages": ["仅含凭据，不含业务数据库"]}
    return {"ok": False, "messages": ["备份点内没有任何可恢复内容"]}


def _export_basic_check(item: dict) -> dict:
    path = Path(item["path"])
    messages: List[str] = []
    if not path.is_file() or path.stat().st_size <= 0:
        return {"ok": False, "messages": ["归档文件不存在或体积为零"]}
    try:
        with open(path, "rb") as handle:
            head = handle.read(len(backup_service.BACKUP_MAGIC))
    except OSError as exc:
        return {"ok": False, "messages": [f"归档无法打开：{exc}"]}
    if head != backup_service.BACKUP_MAGIC:
        return {"ok": False, "messages": ["不是有效的备份文件"]}
    expected = item.get("archive_sha256")
    if not expected:
        messages.append("缺少校验清单，无法核对归档校验和")
        return {"ok": True, "messages": messages}
    actual = backup_service.sha256_file(path)
    if actual != expected:
        return {"ok": False, "messages": ["归档校验和不一致，文件可能被改动"]}
    return {"ok": True, "messages": messages}


def _cold_status_basic_check(status: dict) -> dict:
    archive = Path(status.get("archive") or "")
    if not status.get("ok"):
        return {
            "ok": False,
            "messages": [f"最近一次冷备任务失败：{status.get('error') or '未知原因'}"],
        }
    if not archive.is_file() or archive.stat().st_size <= 0:
        return {"ok": False, "messages": ["冷备归档不存在或体积为零"]}
    expected = status.get("sha256")
    if expected and backup_service.sha256_file(archive) != expected:
        return {"ok": False, "messages": ["冷备归档校验和不一致"]}
    return {"ok": True, "messages": []}


def _verify_cold_archive(archive_path: Path) -> dict:
    """只读校验冷备归档：tar 可打开、清单可读、校验和一致、关键表可读。"""
    messages: List[str] = []
    errors: List[str] = []
    try:
        tar = tarfile.open(archive_path, mode="r")
    except (tarfile.TarError, OSError) as exc:
        return {"ok": False, "messages": [f"冷备归档无法打开：{exc}"]}

    with tar:
        names = set(tar.getnames())
        manifest: dict = {}
        if backup_service.COLD_MANIFEST_NAME in names:
            try:
                manifest = json.loads(
                    tar.extractfile(backup_service.COLD_MANIFEST_NAME).read().decode("utf-8")
                )
            except Exception as exc:
                errors.append(f"清单无法解析：{exc}")
        else:
            messages.append("归档缺少清单，无法核对校验和")

        if backup_service.COLD_CHECKSUMS_NAME in names:
            try:
                sums_text = (
                    tar.extractfile(backup_service.COLD_CHECKSUMS_NAME)
                    .read()
                    .decode("utf-8")
                )
            except Exception as exc:
                errors.append(f"校验和清单无法读取：{exc}")
                sums_text = ""
            for line in sums_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                digest, member = parts[0], parts[1].strip()
                if member not in names:
                    errors.append(f"校验和清单引用了不存在的成员 {member}")
                    continue
                data = tar.extractfile(member).read()
                if hashlib.sha256(data).hexdigest() != digest:
                    errors.append(f"成员校验和不一致：{member}")
        else:
            errors.append("归档缺少校验和清单")

        if "app.db" in names and not errors:
            fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="luyun-cold-verify-")
            os.close(fd)
            try:
                with open(tmp_path, "wb") as handle:
                    handle.write(tar.extractfile("app.db").read())
                db_check = _sqlite_basic_check(
                    Path(tmp_path), expected_rows=manifest.get("row_counts") or {}
                )
                if not db_check["ok"]:
                    errors.extend(db_check["messages"])
                else:
                    messages.extend(db_check["messages"])
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if errors:
        return {"ok": False, "messages": messages + errors}
    return {"ok": True, "messages": messages}


def _cold_scan_basic_check(item: dict) -> dict:
    if item.get("legacy"):
        path = Path(item["archive"])
        db_path = path / "app.db"
        if db_path.is_file():
            return _sqlite_basic_check(db_path)
        return {"ok": True, "messages": ["旧格式冷备目录（任务未报告结果）"]}
    path = Path(item["archive"])
    if not path.is_file() or path.stat().st_size <= 0:
        return {"ok": False, "messages": ["冷备归档不存在或体积为零"]}
    # 状态文件缺失也要真校验一次归档，不能只因为「有文件」就当作可恢复
    result = _verify_cold_archive(path)
    if result["ok"]:
        result["messages"] = list(result["messages"]) + ["冷备任务未报告校验结论"]
    return result


def validate_backup_point(point_id: str) -> dict:
    """对单个备份点只读执行基础校验，返回可读结论（不修改任何备份点）。"""
    point = get_backup_point(point_id)
    if point is None:
        raise FileNotFoundError(point_id)
    result = dict(point["basic_check"])
    result["id"] = point_id
    result["medium"] = point["medium"]
    result["checked_at"] = _now_iso()
    result["recoverable"] = bool(result.get("ok"))
    return result


# ==================== 跨备份点差异 ====================

def current_photo_ids(app_db_path: Optional[str] = None) -> Dict[str, List[str]]:
    return backup_service.classify_hygiene_capture_ids(
        app_db_path or settings.APP_DB_PATH
    )


def photo_difference(
    backup_ids: Dict[str, Sequence[str]],
    *,
    current: Optional[Dict[str, Sequence[str]]] = None,
) -> dict:
    """当前数据库引用的照片中，这一备份点里没有的部分（只提示，不阻止）。"""
    current = current or current_photo_ids()
    diff: Dict[str, List[str]] = {}
    for kind in (PHOTO_STANDARD, PHOTO_OTHER):
        present = set(backup_ids.get(kind) or [])
        missing = sorted(set(current.get(kind) or []) - present)
        if missing:
            diff[kind] = missing
    return {
        "has_difference": bool(diff),
        "missing": diff,
        "standard_missing": len(diff.get(PHOTO_STANDARD, [])),
        "other_missing": len(diff.get(PHOTO_OTHER, [])),
    }


def photo_ids_in_snapshot(ts: str) -> Dict[str, List[str]]:
    snap_dir = backup_service._snapshot_root() / ts
    result: Dict[str, List[str]] = {PHOTO_STANDARD: [], PHOTO_OTHER: []}
    for kind, member_dir in backup_service.PHOTO_MEMBER_DIRS.items():
        directory = snap_dir / member_dir
        if directory.is_dir():
            result[kind] = sorted(p.name for p in directory.iterdir() if p.is_file())
    return result


# ==================== 恢复（覆盖导入 / 回滚快照）====================

def restore_photos(
    standard: Dict[str, bytes],
    other: Dict[str, bytes],
    *,
    capture_root: Optional[Path] = None,
) -> Dict[str, int]:
    """把两类照片写回运行实例；只补齐缺失文件，不删除现有照片。"""
    root = capture_root or backup_service.get_hygiene_capture_root()
    root.mkdir(parents=True, exist_ok=True)
    restored = {PHOTO_STANDARD: 0, PHOTO_OTHER: 0}
    for kind, blobs in ((PHOTO_STANDARD, standard), (PHOTO_OTHER, other)):
        for capture_id, data in blobs.items():
            target = root / capture_id
            if target.name.startswith(".") or "/" in capture_id:
                continue
            try:
                if not target.exists():
                    target.write_bytes(data)
                restored[kind] += 1
            except OSError:
                logger.warning("恢复卫生照片失败: %s", capture_id)
    return restored


def create_pre_restore_snapshot(provenance: str) -> str:
    """恢复动作的前置快照；失败即抛异常，由调用方拒绝该次恢复。"""
    ts = backup_service.create_restore_snapshot(
        settings.APP_DB_PATH,
        backup_service.get_recipes_db_path(),
        backup_service.get_credentials_file_path(),
        provenance=provenance,
    )
    return ts


# ==================== 保留与清理 ====================

def cleanup_preview(
    config: Optional[backup_retention.RetentionConfig] = None,
) -> dict:
    """「按这个配置会删掉哪些备份点」——逐项标出将被删除与受保护的备份点。

    删除集合来自 ``backup_service.plan_*_cleanup``，与实际清理共用同一份计划，
    因此预览说什么就会删什么。
    """
    config = config or backup_retention.cache_get()

    snapshot_plan = backup_service.plan_snapshot_cleanup(config.snapshot_keep)
    export_plan = backup_service.plan_export_cleanup(config.export_keep)
    cold_plan = backup_service.plan_cold_cleanup(config.cold_keep)

    def _snapshot_entry(entry: dict) -> dict:
        return {"id": f"snapshot:{entry['ts']}", **entry}

    def _export_entry(entry: dict) -> dict:
        return {"id": f"export:{entry['name']}", **entry}

    def _cold_entry(entry: dict) -> dict:
        return {"id": f"cold:{entry['ts']}", **entry}

    return {
        "config": config.to_dict(),
        "snapshot": {
            "keep": snapshot_plan["keep"],
            "protected": [_snapshot_entry(e) for e in snapshot_plan["protected"]],
            "delete": [_snapshot_entry(e) for e in snapshot_plan["delete"]],
            "kept": snapshot_plan["kept"],
        },
        "export": {
            "keep": export_plan["keep"],
            "protected": [_export_entry(e) for e in export_plan["protected"]],
            "delete": [_export_entry(e) for e in export_plan["delete"]],
            "kept": export_plan["kept"],
        },
        "cold": {
            "keep": cold_plan["keep"],
            "protected": [_cold_entry(e) for e in cold_plan["protected"]],
            "delete": [_cold_entry(e) for e in cold_plan["delete"]],
            "kept": cold_plan["kept"],
        },
    }


def apply_cleanup(
    config: Optional[backup_retention.RetentionConfig] = None,
) -> dict:
    """确认后立即执行清理；删除集合与预览一致。"""
    config = config or backup_retention.cache_get()
    preview = cleanup_preview(config)

    deleted: List[dict] = []
    for entry in preview["snapshot"]["delete"]:
        snap_dir = backup_service._snapshot_root() / entry["ts"]
        if snap_dir.is_dir():
            shutil.rmtree(snap_dir, ignore_errors=True)
            deleted.append(
                {"id": entry["id"], "size_bytes": entry.get("size_bytes") or 0}
            )
    for entry in preview["export"]["delete"]:
        archive = Path(entry.get("path") or "")
        archive.unlink(missing_ok=True)
        backup_service.export_sidecar_path(archive).unlink(missing_ok=True)
        deleted.append({"id": entry["id"], "size_bytes": entry.get("size_bytes") or 0})
    for entry in preview["cold"]["delete"]:
        run_dir = Path(entry.get("path") or "").parent
        target = run_dir if run_dir.name == entry["ts"] else Path(entry.get("path") or "")
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            deleted.append(
                {"id": entry["id"], "size_bytes": entry.get("size_bytes") or 0}
            )
    return {
        "preview": preview,
        "deleted": deleted,
        "freed_bytes": sum(d.get("size_bytes") or 0 for d in deleted),
    }


# ==================== 备份健康 ====================

def compute_backup_health() -> dict:
    """对全部备份点是否仍可恢复给出当前结论（只读）。"""
    points = list_backup_points()
    total_bytes = sum(p.get("size_bytes") or 0 for p in points)
    usable = [p for p in points if p.get("recoverable")]
    unusable = [p for p in points if not p.get("recoverable")]

    coverage: List[str] = []
    for point in usable:
        for content in point.get("contents") or []:
            if content not in coverage:
                coverage.append(content)

    checks = [
        {
            "code": "has_backup",
            "ok": bool(points),
            "message": "存在备份点" if points else "还没有可用于恢复的备份",
        },
        {
            "code": "has_usable_backup",
            "ok": bool(usable),
            "message": (
                f"{len(usable)} 个备份点校验通过"
                if usable
                else "没有任何备份点通过基础校验"
            ),
        },
        {
            "code": "photos_covered",
            "ok": CONTENT_STANDARD_PHOTOS in coverage
            and CONTENT_OTHER_PHOTOS in coverage,
            "message": (
                "最近可用的备份点包含两类卫生照片"
                if CONTENT_STANDARD_PHOTOS in coverage
                and CONTENT_OTHER_PHOTOS in coverage
                else "可用备份点缺少卫生照片，恢复后检查将没有对照图"
            ),
        },
    ]

    if not points:
        status = "no_backup"
        summary = "还没有可用于恢复的备份"
        next_step = "先在下方生成一份导出备份，并把它复制到别处保存"
    elif not usable:
        status = "unusable"
        summary = "现有备份点都没有通过基础校验"
        next_step = "重新生成一份导出备份；旧的备份点不要作为唯一退路"
    elif not (CONTENT_STANDARD_PHOTOS in coverage and CONTENT_OTHER_PHOTOS in coverage):
        status = "legacy_only"
        summary = "可用备份点来自旧格式，不包含卫生照片"
        next_step = "生成一份新的导出备份，把两类卫生照片一并带走"
    elif unusable:
        status = "degraded"
        summary = f"最近有可用备份，另有 {len(unusable)} 个备份点校验未通过"
        next_step = "确认退路可用后，清理或重新生成校验未通过的备份点"
    else:
        status = "ok"
        summary = "最近备份点校验通过，可恢复"
        next_step = "保持定期导出并复制到别处；需要时可在本页恢复"

    latest = usable[0] if usable else (points[0] if points else None)
    return {
        "status": status,
        "summary": summary,
        "next_step": next_step,
        "last_success_at": latest.get("created_at") if latest else None,
        "last_success_medium": latest.get("medium") if latest else None,
        "last_success_medium_label": latest.get("medium_label") if latest else None,
        "coverage": coverage,
        "coverage_labels": [CONTENT_LABELS.get(c, c) for c in coverage],
        "checks": checks,
        "counts": {
            "total": len(points),
            "usable": len(usable),
            "unusable": len(unusable),
            "by_medium": {
                medium: len([p for p in points if p["medium"] == medium])
                for medium in (MEDIUM_SNAPSHOT, MEDIUM_EXPORT, MEDIUM_COLD)
            },
        },
        "total_bytes": total_bytes,
        "computed_at": _now_iso(),
    }


def set_health_cache(health: dict) -> None:
    global _health_cache
    _health_cache = health


def get_health_cache() -> Optional[dict]:
    return _health_cache


def invalidate_health_cache() -> None:
    """让下一次读取重算备份健康。

    导出、导入恢复与快照回滚都会新增/替换备份点，而 GET /points 与 /health 默认
    返回进程内缓存；不失效的话页面会拿着旧结论（例如「还没有可用于恢复的备份」）
    和刚刷出来的列表自相矛盾。只清缓存、不立刻重算，避免写请求里做一次全量扫描。
    """
    set_health_cache(None)


def refresh_backup_health() -> dict:
    health = compute_backup_health()
    set_health_cache(health)
    return health
