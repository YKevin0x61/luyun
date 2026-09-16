#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统备份点 API：统一清单、两层校验、恢复、保留清理与备份健康。"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import settings
from database import DatabaseManager, get_db
from services import (
    backup_import_staging,
    backup_points,
    backup_retention,
    backup_service,
    credentials_store,
    runtime_settings,
)
from services.backup_service import (
    CONTENT_APP_DB,
    CONTENT_CREDENTIALS,
    CONTENT_LABELS,
    CONTENT_OTHER_PHOTOS,
    CONTENT_RECIPES,
    CONTENT_RUNTIME,
    CONTENT_STANDARD_PHOTOS,
    PHOTO_OTHER,
    PHOTO_STANDARD,
    PROVENANCE_LABELS,
    PROVENANCE_MANUAL,
    PROVENANCE_PRE_IMPORT,
    PROVENANCE_PRE_ROLLBACK,
)
from services.credentials_store import _mask_phone
from api.credentials import _notify_scraper_reload, _notify_scraper_reload_runtime
from api.security import require_session, verify_admin_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/backup",
    tags=["backup"],
    dependencies=[Depends(verify_admin_token)],
)

_SNAPSHOT_TS_RE = re.compile(r"^\d{8}_\d{6}$")

PHOTO_KINDS = (
    (PHOTO_STANDARD, "standard_photos", "标准图"),
    (PHOTO_OTHER, "other_photos", "其它照片"),
)


class BackupExportIn(BaseModel):
    passphrase: str = Field(..., description="导出加密口令")
    include_runtime: bool = Field(False, description="是否打包运行配置")
    include_app_db: bool = Field(True, description="是否打包 app.db")
    include_recipes: bool = Field(True, description="是否打包配方库")
    include_standard_photos: bool = Field(True, description="是否打包标准图（含历史版本）")
    include_other_photos: bool = Field(True, description="是否打包其它卫生照片")


class RetentionIn(BaseModel):
    snapshot_keep: int = Field(..., description="本机回滚快照保留份数")
    cold_keep: int = Field(..., description="冷备保留份数")
    export_keep: int = Field(
        backup_retention.EXPORT_KEEP_DEFAULT,
        description="本机保留的导出备份副本份数",
    )


def _credentials_preview(credentials: dict) -> dict:
    phone = str(credentials.get("phone") or "")
    return {
        "phone_masked": _mask_phone(phone),
        "shop_id": credentials.get("shop_id"),
        "company_id": credentials.get("company_id"),
        "shop_name": credentials.get("shop_name"),
    }


async def _read_upload(file: UploadFile) -> bytes:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    return content


def _photo_counts(parsed: dict) -> Dict[str, int]:
    return {
        kind: len(parsed.get(key) or {})
        for kind, key, _label in PHOTO_KINDS
    }


def _photo_summary(parsed: dict) -> Dict[str, dict]:
    meta = parsed.get("meta") or {}
    declared = meta.get("photos") or {}
    summary: Dict[str, dict] = {}
    for kind, key, label in PHOTO_KINDS:
        blobs = parsed.get(key) or {}
        entry = declared.get(kind) or {}
        summary[kind] = {
            "label": label,
            "included": bool(entry.get("included")),
            "count": len(blobs),
            "bytes": sum(len(b) for b in blobs.values()),
            "declared_count": int(entry.get("count") or 0),
            "missing_references": int(entry.get("missing") or 0),
        }
    return summary


def _missing_contents(parsed: dict) -> List[dict]:
    includes = (parsed.get("meta") or {}).get("includes") or {}
    contents = [
        content
        for content, key in (
            (CONTENT_RUNTIME, "runtime"),
            (CONTENT_APP_DB, "app_db"),
            (CONTENT_RECIPES, "recipes_db"),
            (CONTENT_STANDARD_PHOTOS, "standard_photos"),
            (CONTENT_OTHER_PHOTOS, "other_photos"),
        )
        if includes.get(key)
    ]
    return backup_points.missing_contents(contents)


def _default_apply(parsed: dict, diff: dict) -> Dict[str, bool]:
    includes = (parsed.get("meta") or {}).get("includes") or {}
    missing = diff.get("missing") or {}
    return {
        CONTENT_CREDENTIALS: True,
        CONTENT_RUNTIME: bool(includes.get("runtime")),
        CONTENT_APP_DB: bool(includes.get("app_db")),
        CONTENT_RECIPES: bool(includes.get("recipes_db")),
        CONTENT_STANDARD_PHOTOS: bool(includes.get("standard_photos"))
        and not missing.get(PHOTO_STANDARD),
        CONTENT_OTHER_PHOTOS: bool(includes.get("other_photos"))
        and not missing.get(PHOTO_OTHER),
    }


def _validation(parsed: dict) -> dict:
    """两层校验：备份点内不一致阻止恢复；跨备份点差异只提示。"""
    meta = parsed.get("meta") or {}
    counts = _photo_counts(parsed)
    errors = list(parsed.get("archive_integrity_errors") or [])
    errors.extend(backup_service.archive_integrity_errors(meta, counts))

    backup_ids = {
        PHOTO_STANDARD: sorted((parsed.get("standard_photos") or {}).keys()),
        PHOTO_OTHER: sorted((parsed.get("other_photos") or {}).keys()),
    }
    diff = backup_points.photo_difference(backup_ids)
    declared_consistency = meta.get("consistency") or {}
    if declared_consistency and not declared_consistency.get("ok", True):
        errors.extend(declared_consistency.get("errors") or [])

    return {
        "in_backup": {"ok": not errors, "errors": errors},
        "cross_point": diff,
    }


async def _collect_export_payload(payload: BackupExportIn, db: DatabaseManager) -> dict:
    runtime_data = None
    if payload.include_runtime:
        runtime_data = await runtime_settings.load_runtime_settings(db)

    app_db_bytes = None
    if payload.include_app_db:
        fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="luyun-export-")
        os.close(fd)
        try:
            await db.export_merged_sqlite_file(tmp_path)
            with open(tmp_path, "rb") as f:
                app_db_bytes = f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    recipes_db_bytes = None
    if payload.include_recipes:
        recipes_path = backup_service.get_recipes_db_path()
        recipes_db_bytes = backup_service.export_recipes_db_bytes(recipes_path)

    photo_info: dict = {"members": {}, "manifest": {}, "missing": {}}
    if payload.include_standard_photos or payload.include_other_photos:
        photo_info = backup_service.collect_hygiene_photo_members()

    return {
        "runtime_data": runtime_data,
        "app_db_bytes": app_db_bytes,
        "recipes_db_bytes": recipes_db_bytes,
        "photo_info": photo_info,
    }


@router.post("/export")
async def export_backup(
    payload: BackupExportIn,
    db: DatabaseManager = Depends(get_db),
    session_id: str = Depends(require_session),
):
    """导出口令加密备份包，并在本机登记为一条导出备份点。"""
    collected = await _collect_export_payload(payload, db)
    photo_info = collected["photo_info"]
    consistency = backup_service.photo_consistency(
        photo_info.get("manifest") or {},
        photo_info.get("missing") or {},
    )

    try:
        blob, meta = backup_service.build_export_backup(
            payload.passphrase,
            include_runtime=payload.include_runtime,
            runtime_data=collected["runtime_data"],
            include_app_db=payload.include_app_db,
            app_db_bytes=collected["app_db_bytes"],
            include_recipes=payload.include_recipes,
            recipes_db_bytes=collected["recipes_db_bytes"],
            include_standard_photos=payload.include_standard_photos,
            include_other_photos=payload.include_other_photos,
            photo_members=photo_info.get("members"),
            photo_manifest=photo_info.get("manifest"),
            photo_missing=photo_info.get("missing"),
            consistency=consistency,
            provenance=PROVENANCE_MANUAL,
            app_version=settings.APP_VERSION,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("导出系统备份失败: %s", exc)
        raise HTTPException(status_code=500, detail="导出系统备份失败")

    ts = datetime.now(credentials_store.CHINA_TZ).strftime("%Y%m%d_%H%M%S")
    filename = f"luyun_backup_{ts}.luyunbak"
    export_dir = backup_service._export_root()
    export_dir.mkdir(parents=True, exist_ok=True)
    archive_path = export_dir / filename
    try:
        archive_path.write_bytes(blob)
        backup_service.write_export_sidecar(archive_path, meta)
        # 本机副本按保留配置收敛，避免导出备份无限占用磁盘
        config = await backup_retention.load_retention(db)
        backup_service.prune_export_backups(config.export_keep)
    except OSError as exc:
        # 没有侧车清单的归档无法核对校验和，会以「缺少校验清单」出现在列表里；
        # 宁可删掉本机副本，也不留一条看起来可信却无法校验的备份点。下载不受影响。
        logger.error("登记导出备份失败，已删除本机副本: %s", exc)
        try:
            archive_path.unlink(missing_ok=True)
            backup_service.export_sidecar_path(archive_path).unlink(missing_ok=True)
        except OSError:
            pass

    logger.info(
        "📦 [审计] 导出系统备份（runtime=%s app_db=%s recipes=%s 标准图=%s 其它照片=%s）",
        payload.include_runtime,
        payload.include_app_db,
        payload.include_recipes,
        payload.include_standard_photos,
        payload.include_other_photos,
    )

    return StreamingResponse(
        iter([blob]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _staging_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc) or "导入暂存与当前会话不匹配")
    if isinstance(exc, TimeoutError):
        return HTTPException(status_code=410, detail=str(exc) or "预览已过期，请重新上传备份文件")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc) or "导入暂存不存在")
    return HTTPException(status_code=400, detail=str(exc))


async def _apply_parsed_backup(
    parsed: dict,
    *,
    mode: str,
    apply_credentials: bool,
    apply_runtime: bool,
    apply_app_db: bool,
    apply_recipes: bool,
    apply_standard_photos: bool,
    apply_other_photos: bool,
    force: bool,
    db: DatabaseManager,
) -> dict:
    """将已解密的备份内容写入系统（merge 或 overwrite）。"""
    validation = _validation(parsed)
    if not validation["in_backup"]["ok"]:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "backup_corrupt",
                "message": "这份备份自身不一致，已阻止恢复："
                + "；".join(validation["in_backup"]["errors"]),
                "errors": validation["in_backup"]["errors"],
            },
        )

    diff = validation["cross_point"]
    missing = diff.get("missing") or {}
    blocked: List[str] = []
    if apply_standard_photos and missing.get(PHOTO_STANDARD):
        blocked.append(CONTENT_LABELS[CONTENT_STANDARD_PHOTOS])
    if apply_other_photos and missing.get(PHOTO_OTHER):
        blocked.append(CONTENT_LABELS[CONTENT_OTHER_PHOTOS])
    if blocked and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "photo_mismatch",
                "message": "当前数据引用的照片不在这份备份里："
                + "、".join(blocked)
                + "；请确认后强制继续，或取消勾选这类照片",
                "missing": {
                    PHOTO_STANDARD: len(missing.get(PHOTO_STANDARD) or []),
                    PHOTO_OTHER: len(missing.get(PHOTO_OTHER) or []),
                },
            },
        )

    will_touch_db = (
        (apply_app_db and parsed["app_db_bytes"] is not None)
        or (apply_recipes and parsed["recipes_db_bytes"] is not None)
    )
    will_touch_photos = (
        (apply_standard_photos and bool(parsed.get("standard_photos")))
        or (apply_other_photos and bool(parsed.get("other_photos")))
    )
    # 任何一类内容被写回都算一次恢复：凭据 / 运行配置同样要能退回去
    will_restore = bool(
        (apply_credentials and parsed.get("credentials"))
        or (apply_runtime and parsed["runtime"] is not None)
        or will_touch_db
        or will_touch_photos
    )

    snapshot_ts: Optional[str] = None
    if will_restore:
        try:
            snapshot_ts = backup_points.create_pre_restore_snapshot(
                PROVENANCE_PRE_IMPORT
            )
        except Exception as exc:
            logger.error("前置快照创建失败，拒绝执行恢复: %s", exc)
            raise HTTPException(
                status_code=500,
                detail="前置快照创建失败，已拒绝执行本次恢复（当前数据保持不变）",
            )

    applied: Dict[str, bool] = {content: False for content in CONTENT_LABELS}

    if apply_credentials:
        try:
            credentials_store.save_credentials(parsed["credentials"])
            applied[CONTENT_CREDENTIALS] = True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"备份中的凭据无效：{exc}")

    if apply_runtime and parsed["runtime"] is not None:
        try:
            await runtime_settings.save_runtime_settings(db, parsed["runtime"])
            applied[CONTENT_RUNTIME] = True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"备份中的运行配置无效：{exc}")

    if apply_app_db and parsed["app_db_bytes"] is not None:
        try:
            if mode == "overwrite":
                await backup_service.overwrite_app_db_from_bytes(
                    db, parsed["app_db_bytes"]
                )
            else:
                await backup_service.merge_app_db_from_bytes(
                    db, parsed["app_db_bytes"]
                )
            applied[CONTENT_APP_DB] = True
        except Exception as exc:
            logger.error("应用 app.db 备份失败: %s", exc)
            raise HTTPException(status_code=500, detail="应用 app.db 备份失败")

    if apply_recipes and parsed["recipes_db_bytes"] is not None:
        from main import recipe_store

        if recipe_store is None:
            raise HTTPException(status_code=503, detail="配方库未就绪")
        try:
            if mode == "overwrite":
                await backup_service.overwrite_recipes_from_bytes(
                    recipe_store, parsed["recipes_db_bytes"]
                )
            else:
                await backup_service.merge_recipes_from_bytes(
                    recipe_store, parsed["recipes_db_bytes"]
                )
            applied[CONTENT_RECIPES] = True
        except Exception as exc:
            logger.error("应用配方库备份失败: %s", exc)
            raise HTTPException(status_code=500, detail="应用配方库备份失败")

    restored_photos = {PHOTO_STANDARD: 0, PHOTO_OTHER: 0}
    if apply_standard_photos or apply_other_photos:
        restored_photos = backup_points.restore_photos(
            parsed.get("standard_photos") if apply_standard_photos else {},
            parsed.get("other_photos") if apply_other_photos else {},
        )
        applied[CONTENT_STANDARD_PHOTOS] = bool(apply_standard_photos)
        applied[CONTENT_OTHER_PHOTOS] = bool(apply_other_photos)

    logger.info(
        "📥 [审计] 已导入系统备份 mode=%s applied=%s snapshot=%s",
        mode,
        applied,
        snapshot_ts,
    )

    if applied[CONTENT_CREDENTIALS]:
        await _notify_scraper_reload()
    if applied[CONTENT_RUNTIME]:
        await _notify_scraper_reload_runtime(db)

    session_invalidated = applied[CONTENT_APP_DB] or applied[CONTENT_CREDENTIALS]
    return {
        "success": True,
        "mode": mode,
        "applied": applied,
        "applied_labels": [
            CONTENT_LABELS[c] for c, done in applied.items() if done
        ],
        "photos_restored": restored_photos,
        "snapshot_ts": snapshot_ts,
        "session_invalidated": session_invalidated,
    }


@router.post("/import/preview")
async def import_backup_preview(
    file: UploadFile = File(...),
    passphrase: str = Form(...),
    session_id: str = Depends(require_session),
):
    """解密备份、写入导入暂存并返回解析结果与两层校验结论。"""
    backup_import_staging.cleanup_expired_staging()
    content = await _read_upload(file)
    try:
        parsed = backup_service.parse_backup(content, passphrase)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    import_token = backup_import_staging.create_staging(session_id, parsed)
    meta = parsed["meta"]
    includes = meta.get("includes") or {}
    validation = _validation(parsed)
    diff = validation["cross_point"]
    will_touch = bool(
        parsed["app_db_bytes"]
        or parsed["recipes_db_bytes"]
        or parsed.get("standard_photos")
        or parsed.get("other_photos")
    )
    return {
        "success": True,
        "import_token": import_token,
        "meta": meta,
        "includes": includes,
        "credentials_preview": _credentials_preview(parsed["credentials"]),
        "has_runtime": parsed["runtime"] is not None,
        "has_app_db": parsed["app_db_bytes"] is not None,
        "has_recipes": parsed["recipes_db_bytes"] is not None,
        "photos": _photo_summary(parsed),
        "missing": _missing_contents(parsed),
        "validation": validation,
        "restore_allowed": validation["in_backup"]["ok"],
        "requires_force": diff["has_difference"],
        "default_apply": _default_apply(parsed, diff),
        "pre_snapshot": {
            "will_create": will_touch,
            "provenance": PROVENANCE_PRE_IMPORT,
            "provenance_label": PROVENANCE_LABELS[PROVENANCE_PRE_IMPORT],
        },
        "pre_snapshot_provenance": PROVENANCE_PRE_IMPORT,
    }


@router.post("/import/apply")
async def import_backup_apply(
    import_token: str = Form(...),
    mode: str = Form(...),
    apply_credentials: bool = Form(True),
    apply_runtime: bool = Form(False),
    apply_app_db: bool = Form(True),
    apply_recipes: bool = Form(True),
    apply_standard_photos: bool = Form(True),
    apply_other_photos: bool = Form(True),
    force: bool = Form(False),
    session_id: str = Depends(require_session),
    db: DatabaseManager = Depends(get_db),
):
    """从导入暂存应用备份内容（merge 或 overwrite）。"""
    if mode not in ("merge", "overwrite"):
        raise HTTPException(status_code=400, detail="mode 必须是 merge 或 overwrite")

    backup_import_staging.cleanup_expired_staging()
    try:
        parsed = backup_import_staging.consume_staging(import_token, session_id)
    except (FileNotFoundError, PermissionError, TimeoutError) as exc:
        raise _staging_http_error(exc) from exc

    return await _apply_parsed_backup(
        parsed,
        mode=mode,
        apply_credentials=apply_credentials,
        apply_runtime=apply_runtime,
        apply_app_db=apply_app_db,
        apply_recipes=apply_recipes,
        apply_standard_photos=apply_standard_photos,
        apply_other_photos=apply_other_photos,
        force=force,
        db=db,
    )


@router.get("/snapshots")
async def list_backup_snapshots():
    """列出本地回滚快照（兼容入口）。"""
    return {"success": True, "snapshots": backup_service.list_snapshots()}


@router.post("/snapshots/{ts}/rollback")
async def rollback_snapshot(
    ts: str,
    apply_standard_photos: bool = True,
    apply_other_photos: bool = True,
    db: DatabaseManager = Depends(get_db),
    session_id: str = Depends(require_session),
):
    """回滚到指定本机回滚快照；写入前先给当前状态建一份前置快照。"""
    if not _SNAPSHOT_TS_RE.fullmatch(ts):
        raise HTTPException(status_code=404, detail="快照不存在")

    snap_dir = backup_service._snapshot_root() / ts
    if not snap_dir.is_dir():
        raise HTTPException(status_code=404, detail="快照不存在")

    basic = backup_points.validate_backup_point(f"snapshot:{ts}")
    if not basic.get("ok"):
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "backup_corrupt",
                "message": "这份快照没有通过基础校验，已阻止恢复："
                + "；".join(basic.get("messages") or []),
            },
        )

    try:
        pre_ts = backup_points.create_pre_restore_snapshot(PROVENANCE_PRE_ROLLBACK)
    except Exception as exc:
        logger.error("前置快照创建失败，拒绝执行回滚: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="前置快照创建失败，已拒绝执行本次回滚（当前数据保持不变）",
        )

    applied: Dict[str, bool] = {content: False for content in CONTENT_LABELS}
    snap_app = snap_dir / "app.db"
    if snap_app.is_file():
        with open(snap_app, "rb") as f:
            await backup_service.overwrite_app_db_from_bytes(db, f.read())
        applied[CONTENT_APP_DB] = True

    snap_recipes = snap_dir / "recipes.db"
    if snap_recipes.is_file():
        from main import recipe_store

        if recipe_store is None:
            raise HTTPException(status_code=503, detail="配方库未就绪")
        with open(snap_recipes, "rb") as f:
            await backup_service.overwrite_recipes_from_bytes(recipe_store, f.read())
        applied[CONTENT_RECIPES] = True

    snap_cred = snap_dir / "credentials.enc"
    if snap_cred.is_file():
        cred_path = backup_service.get_credentials_file_path()
        os.makedirs(os.path.dirname(cred_path), exist_ok=True)
        import shutil

        shutil.copy2(str(snap_cred), cred_path)
        try:
            os.chmod(cred_path, 0o600)
        except OSError:
            pass
        # 密钥文件与凭据成对恢复，否则凭据无法解密
        snap_key = snap_dir / ".cred_key"
        if snap_key.is_file():
            key_path = os.path.join(os.path.dirname(cred_path), ".cred_key")
            shutil.copy2(str(snap_key), key_path)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
        credentials_store.reload()
        applied[CONTENT_CREDENTIALS] = True

    restored_photos = {PHOTO_STANDARD: 0, PHOTO_OTHER: 0}
    kinds = []
    if apply_standard_photos:
        kinds.append(PHOTO_STANDARD)
    if apply_other_photos:
        kinds.append(PHOTO_OTHER)
    if kinds:
        restored_photos = backup_service.restore_snapshot_photos(ts, kinds)
        applied[CONTENT_STANDARD_PHOTOS] = bool(apply_standard_photos)
        applied[CONTENT_OTHER_PHOTOS] = bool(apply_other_photos)

    logger.info(
        "⏪ [审计] 快照回滚完成 ts=%s applied=%s pre_snapshot=%s",
        ts,
        applied,
        pre_ts,
    )
    await _notify_scraper_reload()
    await _notify_scraper_reload_runtime(db)

    return {
        "success": True,
        "ts": ts,
        "applied": applied,
        "applied_labels": [CONTENT_LABELS[c] for c, done in applied.items() if done],
        "photos_restored": restored_photos,
        "snapshot_ts": pre_ts,
        "session_invalidated": applied[CONTENT_APP_DB] or applied[CONTENT_CREDENTIALS],
    }


# ==================== 备份点清单 / 校验 / 健康 ====================

@router.get("/points")
async def list_points(db: DatabaseManager = Depends(get_db)):
    """统一备份点列表 + 备份健康结论 + 不备份清单。"""
    points = backup_points.list_backup_points()
    health = backup_points.get_health_cache()
    if health is None:
        health = backup_points.refresh_backup_health()
    return {
        "success": True,
        "points": points,
        "health": health,
        "not_backed_up": backup_points.NOT_BACKED_UP,
        "medium_labels": backup_points.MEDIUM_LABELS,
        "medium_purposes": backup_points.MEDIUM_PURPOSES,
    }


@router.post("/points/{point_id}/validate")
async def validate_point(point_id: str):
    """对单个备份点只读执行基础校验。"""
    try:
        return {"success": True, "result": backup_points.validate_backup_point(point_id)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="备份点不存在")


@router.get("/health")
async def backup_health():
    """备份健康结论（启动时算一次，可手动重跑）。"""
    health = backup_points.get_health_cache()
    if health is None:
        health = backup_points.refresh_backup_health()
    return {"success": True, "health": health}


@router.post("/health/refresh")
async def refresh_backup_health():
    """手动重跑备份健康。"""
    return {"success": True, "health": backup_points.refresh_backup_health()}


# ==================== 保留配置与清理 ====================

@router.get("/retention")
async def get_retention(db: DatabaseManager = Depends(get_db)):
    """当前保留配置、默认值与上限，以及按当前配置的删除预览。"""
    config = await backup_retention.load_retention(db)
    return {
        "success": True,
        "config": config.to_dict(),
        "limits": backup_retention.limits(),
        "preview": backup_points.cleanup_preview(config),
    }


@router.put("/retention")
async def put_retention(
    payload: RetentionIn,
    db: DatabaseManager = Depends(get_db),
    session_id: str = Depends(require_session),
):
    """保存保留配置；确认后立即执行清理并刷新列表。"""
    try:
        config = backup_retention.validate_retention(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    preview = backup_points.cleanup_preview(config)
    try:
        saved = await backup_retention.save_retention_config(db, config)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cleanup = backup_points.apply_cleanup(saved)
    backup_points.refresh_backup_health()
    return {
        "success": True,
        "config": saved.to_dict(),
        "limits": backup_retention.limits(),
        "cleanup": cleanup,
        "preview": preview,
    }


@router.post("/cleanup/preview")
async def cleanup_preview(payload: Optional[RetentionIn] = None):
    """预览「将删除哪些备份点」，不修改任何内容。"""
    config = None
    if payload is not None:
        try:
            config = backup_retention.validate_retention(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return {
        "success": True,
        "preview": backup_points.cleanup_preview(config),
    }


@router.post("/cleanup")
async def run_cleanup(
    db: DatabaseManager = Depends(get_db),
    session_id: str = Depends(require_session),
):
    """按当前保留配置执行清理。"""
    config = await backup_retention.load_retention(db)
    result = backup_points.apply_cleanup(config)
    backup_points.refresh_backup_health()
    return {"success": True, **result}
