#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统完整备份 API（v2 加密归档 + 快照回滚）。"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import settings
from database import DatabaseManager, get_db
from services import backup_import_staging, backup_service, credentials_store, runtime_settings
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


class BackupExportIn(BaseModel):
    passphrase: str = Field(..., description="导出加密口令")
    include_runtime: bool = Field(False, description="是否打包运行配置")
    include_app_db: bool = Field(True, description="是否打包 app.db")
    include_recipes: bool = Field(True, description="是否打包配方库")


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


@router.post("/export")
async def export_backup(
    payload: BackupExportIn,
    db: DatabaseManager = Depends(get_db),
):
    """导出口令加密的 v2 系统备份包（二进制流）。"""
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

    try:
        blob = backup_service.build_backup(
            payload.passphrase,
            include_runtime=payload.include_runtime,
            runtime_data=runtime_data,
            include_app_db=payload.include_app_db,
            app_db_bytes=app_db_bytes,
            include_recipes=payload.include_recipes,
            recipes_db_bytes=recipes_db_bytes,
            app_version=settings.APP_VERSION,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("导出系统备份失败: %s", exc)
        raise HTTPException(status_code=500, detail="导出系统备份失败")

    ts = datetime.now(credentials_store.CHINA_TZ).strftime("%Y%m%d_%H%M%S")
    filename = f"luyun_backup_{ts}.luyunbak"
    logger.info(
        "📦 [审计] 导出系统备份 v2（runtime=%s app_db=%s recipes=%s）",
        payload.include_runtime,
        payload.include_app_db,
        payload.include_recipes,
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
    db: DatabaseManager,
) -> dict:
    """将已解密的备份内容写入系统（merge 或 overwrite）。"""
    snapshot_ts: Optional[str] = None
    will_touch_db = (
        (apply_app_db and parsed["app_db_bytes"] is not None)
        or (apply_recipes and parsed["recipes_db_bytes"] is not None)
    )
    if mode == "overwrite" and will_touch_db:
        snapshot_ts = backup_service.create_restore_snapshot(
            settings.APP_DB_PATH,
            backup_service.get_recipes_db_path(),
            backup_service.get_credentials_file_path(),
        )

    applied: Dict[str, bool] = {
        "credentials": False,
        "runtime": False,
        "app_db": False,
        "recipes": False,
    }

    if apply_credentials:
        try:
            credentials_store.save_credentials(parsed["credentials"])
            applied["credentials"] = True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"备份中的凭据无效：{exc}")

    if apply_runtime and parsed["runtime"] is not None:
        try:
            await runtime_settings.save_runtime_settings(db, parsed["runtime"])
            applied["runtime"] = True
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
            applied["app_db"] = True
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
            applied["recipes"] = True
        except Exception as exc:
            logger.error("应用配方库备份失败: %s", exc)
            raise HTTPException(status_code=500, detail="应用配方库备份失败")

    logger.info(
        "📥 [审计] 已导入系统备份 mode=%s applied=%s snapshot=%s",
        mode,
        applied,
        snapshot_ts,
    )

    if applied["credentials"]:
        await _notify_scraper_reload()
    if applied["runtime"]:
        await _notify_scraper_reload_runtime(db)

    return {
        "success": True,
        "mode": mode,
        "applied": applied,
        "snapshot_ts": snapshot_ts,
    }


@router.post("/import/preview")
async def import_backup_preview(
    file: UploadFile = File(...),
    passphrase: str = Form(...),
    session_id: str = Depends(require_session),
):
    """解密备份、写入导入暂存并返回脱敏预览。"""
    backup_import_staging.cleanup_expired_staging()
    content = await _read_upload(file)
    try:
        parsed = backup_service.parse_backup(content, passphrase)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    import_token = backup_import_staging.create_staging(session_id, parsed)
    meta = parsed["meta"]
    includes = meta.get("includes") or {}
    return {
        "success": True,
        "import_token": import_token,
        "meta": meta,
        "includes": includes,
        "credentials_preview": _credentials_preview(parsed["credentials"]),
        "has_runtime": parsed["runtime"] is not None,
        "has_app_db": parsed["app_db_bytes"] is not None,
        "has_recipes": parsed["recipes_db_bytes"] is not None,
    }


@router.post("/import/apply")
async def import_backup_apply(
    import_token: str = Form(...),
    mode: str = Form(...),
    apply_credentials: bool = Form(True),
    apply_runtime: bool = Form(False),
    apply_app_db: bool = Form(True),
    apply_recipes: bool = Form(True),
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
        db=db,
    )


@router.get("/snapshots")
async def list_backup_snapshots():
    """列出本地回滚快照。"""
    return {"success": True, "snapshots": backup_service.list_snapshots()}


@router.post("/snapshots/{ts}/rollback")
async def rollback_snapshot(
    ts: str,
    db: DatabaseManager = Depends(get_db),
):
    """从指定快照回滚 app.db / recipes.db / 凭据文件。"""
    if not _SNAPSHOT_TS_RE.fullmatch(ts):
        raise HTTPException(status_code=404, detail="快照不存在")

    snap_dir = backup_service._snapshot_root() / ts
    if not snap_dir.is_dir():
        raise HTTPException(status_code=404, detail="快照不存在")

    snap_app = snap_dir / "app.db"
    if snap_app.is_file():
        with open(snap_app, "rb") as f:
            await backup_service.overwrite_app_db_from_bytes(db, f.read())

    snap_recipes = snap_dir / "recipes.db"
    if snap_recipes.is_file():
        from main import recipe_store

        if recipe_store is None:
            raise HTTPException(status_code=503, detail="配方库未就绪")
        with open(snap_recipes, "rb") as f:
            await backup_service.overwrite_recipes_from_bytes(
                recipe_store, f.read()
            )

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
        credentials_store.reload()

    logger.info("⏪ [审计] 快照回滚完成 ts=%s", ts)
    await _notify_scraper_reload()
    await _notify_scraper_reload_runtime(db)

    return {"success": True, "ts": ts}
