#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统备份点 API：统一清单、两层校验、恢复、保留清理与备份健康。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
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
    CONTENT_APP_PG,
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
    # 业务数据只有整库 pg_dump 一种形态（成员 app.pgdump）；配方表在同一个库里，
    # 随它一起走，因此没有单独的「配方库」开关。
    include_app_db: bool = Field(True, description="是否打包业务数据（整库 pg_dump）")
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
    # 与备份点列表共用同一份口径：导出包必然含凭据（构建端无条件打包）
    return backup_points.missing_contents(backup_points.export_contents(includes))


def _default_apply(parsed: dict, diff: dict) -> Dict[str, bool]:
    includes = (parsed.get("meta") or {}).get("includes") or {}
    missing = diff.get("missing") or {}
    return {
        CONTENT_CREDENTIALS: True,
        CONTENT_RUNTIME: bool(includes.get("runtime")),
        CONTENT_APP_DB: bool(includes.get("app_db")),
        CONTENT_APP_PG: bool(includes.get("app_pg")),
        CONTENT_RECIPES: bool(includes.get("recipes_db")),
        CONTENT_STANDARD_PHOTOS: bool(includes.get("standard_photos"))
        and not missing.get(PHOTO_STANDARD),
        CONTENT_OTHER_PHOTOS: bool(includes.get("other_photos"))
        and not missing.get(PHOTO_OTHER),
    }


async def _validation(parsed: dict, db: DatabaseManager) -> dict:
    """两层校验：备份点内不一致阻止恢复；跨备份点差异只提示。"""
    meta = parsed.get("meta") or {}
    counts = _photo_counts(parsed)
    errors = list(parsed.get("archive_integrity_errors") or [])
    errors.extend(backup_service.archive_integrity_errors(meta, counts))

    backup_ids = {
        PHOTO_STANDARD: sorted((parsed.get("standard_photos") or {}).keys()),
        PHOTO_OTHER: sorted((parsed.get("other_photos") or {}).keys()),
    }
    diff = await backup_points.photo_difference(backup_ids, db=db)
    declared_consistency = meta.get("consistency") or {}
    # 只有「备份自身坏了」（blocking）才算 in_backup 失败；照片在源磁盘上缺失
    # 这类完整度问题不阻断恢复（跨备份点差异另走 cross_point + 强制继续那条路）。
    if (
        declared_consistency
        and declared_consistency.get("blocking")
        and not declared_consistency.get("ok", True)
    ):
        errors.extend(declared_consistency.get("errors") or [])

    return {
        "in_backup": {"ok": not errors, "errors": errors},
        "cross_point": diff,
    }


def _cleanup_temp_files(paths: Sequence[str]) -> None:
    """删除导出过程中落下的临时成员文件（成品包不在其中）。"""
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def _read_temp_bytes(path: Optional[str]) -> Optional[bytes]:
    if not path:
        return None
    with open(path, "rb") as handle:
        return handle.read()


def _report_progress(
    progress: Optional[Callable[[str, int, int, str], None]],
    stage: str,
    done: int = 0,
    total: int = 0,
    unit: str = "count",
) -> None:
    if progress is None:
        return
    try:
        progress(stage, done, total, unit)
    except Exception:  # noqa: BLE001 —— 进度上报不该把导出搞挂
        logger.debug("导出进度回调失败 stage=%s", stage, exc_info=True)


async def _collect_export_payload(
    payload: BackupExportIn,
    db: DatabaseManager,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> dict:
    """收集导出成员。

    大成员（``app.pgdump`` / 照片）只给**磁盘路径**：打包时 tar 直接流式读走，
    不再先把它们读成 bytes 堆在内存里。调用方负责用返回的 ``temp_paths``
    清理临时文件。
    """
    _report_progress(progress, "collecting")
    runtime_data = None
    if payload.include_runtime:
        runtime_data = await runtime_settings.load_runtime_settings(db)

    temp_paths: List[str] = []
    app_pg_path: Optional[str] = None
    photo_info: dict = {"members": [], "manifest": {}, "missing": {}}
    try:
        if payload.include_app_db:
            _report_progress(progress, "app_data")
            # 业务数据 = 整库 pg_dump（成员 app.pgdump），恢复端走 pg_restore 覆盖。
            # 配方表在同一个库里，随这份 dump 一起走。
            fd, app_pg_path = tempfile.mkstemp(
                suffix=".pgdump", prefix="luyun-export-"
            )
            os.close(fd)
            temp_paths.append(app_pg_path)
            await backup_service.export_pg_dump_to_file(
                app_pg_path,
                lambda written: _report_progress(
                    progress, "app_data", written, 0, "bytes"
                ),
            )

        if payload.include_standard_photos or payload.include_other_photos:
            _report_progress(progress, "photos_scan")
            photo_info = await backup_service.collect_hygiene_photo_paths(db)
    except Exception:
        _cleanup_temp_files(temp_paths)
        raise

    return {
        "runtime_data": runtime_data,
        "app_pg_path": app_pg_path,
        "photo_info": photo_info,
        "temp_paths": temp_paths,
    }


# ==================== 任务式导出（进度可见）====================

_EXPORT_JOBS: Dict[str, Dict[str, Any]] = {}
_EXPORT_JOB_TTL_SECONDS = 30 * 60
# 导出是重活（pg_dump、tar、加密都可能跑到分钟级），串行更稳也更好解释
_EXPORT_MAX_RUNNING = 1


def _prune_export_jobs(now: float) -> None:
    """清掉过期任务记录。**不删成品包**：它是本机导出备份点，归保留配置管。"""
    for job_id, job in list(_EXPORT_JOBS.items()):
        if now - job["started_at"] < _EXPORT_JOB_TTL_SECONDS:
            continue
        _cleanup_temp_files(job.get("temp_paths") or [])
        _EXPORT_JOBS.pop(job_id, None)


def _job_public_state(job: dict) -> dict:
    return {
        "state": job["state"],
        "stage": job["stage"],
        "done": job["done"],
        "total": job["total"],
        # unit 决定前端怎么读 done/total："count" 是张数（照片），"bytes" 是字节
        "unit": job.get("unit") or "count",
        "error": job["error"],
        "bytes": job.get("bytes") or 0,
        "name": job.get("name") or "",
    }


async def _register_export_archive(
    archive_path: Path,
    meta: dict,
    db: DatabaseManager,
) -> None:
    """写侧车清单 + 按保留配置收敛本机副本（同步端点与任务共用）。"""
    try:
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


async def _run_export_job(
    job_id: str,
    payload: BackupExportIn,
    db: DatabaseManager,
) -> None:
    """后台把导出包写进 backup_exports，进度写在任务表里供前端轮询。"""
    job = _EXPORT_JOBS[job_id]
    collected: dict = {}

    def _progress(
        stage: str,
        done: int = 0,
        total: int = 0,
        unit: str = "count",
    ) -> None:
        job["stage"] = stage
        job["done"] = done
        job["total"] = total
        job["unit"] = unit

    archive_path: Optional[Path] = None
    try:
        collected = await _collect_export_payload(payload, db, _progress)
        job["temp_paths"] = collected["temp_paths"]
        photo_info = collected["photo_info"]
        consistency = backup_service.photo_consistency(
            photo_info.get("manifest") or {},
            photo_info.get("missing") or {},
        )

        ts = datetime.now(credentials_store.CHINA_TZ).strftime("%Y%m%d_%H%M%S")
        filename = f"luyun_backup_{ts}.luyunbak"
        export_dir = backup_service._export_root()
        export_dir.mkdir(parents=True, exist_ok=True)
        archive_path = export_dir / filename

        # tar + 加密是同步重活：扔进线程跑，别把事件循环（KDS、爬虫）堵住
        meta = await asyncio.to_thread(
            backup_service.build_export_backup_to_file,
            str(archive_path),
            payload.passphrase,
            include_runtime=payload.include_runtime,
            runtime_data=collected["runtime_data"],
            include_app_db=payload.include_app_db,
            app_pg_source=collected["app_pg_path"],
            include_standard_photos=payload.include_standard_photos,
            include_other_photos=payload.include_other_photos,
            photo_members=photo_info.get("members"),
            photo_manifest=photo_info.get("manifest"),
            photo_missing=photo_info.get("missing"),
            consistency=consistency,
            provenance=PROVENANCE_MANUAL,
            app_version=settings.APP_VERSION,
            progress=_progress,
        )
        await _register_export_archive(archive_path, meta, db)

        job.update(
            state="done",
            stage="done",
            done=job["total"],
            name=filename,
            bytes=meta.get("archive_bytes") or 0,
            path=str(archive_path),
        )
        logger.info(
            "📦 [审计] 导出系统备份（runtime=%s app_db=%s 整库 dump=%s "
            "标准图=%s 其它照片=%s，%.1f MB）",
            payload.include_runtime,
            payload.include_app_db,
            bool(collected.get("app_pg_path")),
            payload.include_standard_photos,
            payload.include_other_photos,
            (meta.get("archive_bytes") or 0) / 1024 / 1024,
        )
        # 本机新增了一个导出备份点：让健康结论下次读取时重算，别和列表打架。
        backup_points.invalidate_health_cache()
    except ValueError as exc:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        job.update(state="failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001 —— 后台任务必须自己收口，否则永远 running
        logger.exception("导出系统备份失败 job=%s", job_id)
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        job.update(state="failed", error=f"导出失败：{exc}")
    finally:
        _cleanup_temp_files(collected.get("temp_paths") or [])
        job["temp_paths"] = []


@router.post("/export/jobs")
async def start_export_backup(
    payload: BackupExportIn,
    db: DatabaseManager = Depends(get_db),
    session_id: str = Depends(require_session),
) -> Dict[str, Any]:
    """起一个导出任务，立刻返回 job_id；打包进度由状态接口轮询。

    同步返回会让浏览器干等（100 MB 级的库，实测 4 秒起步，大库更久），期间界面上
    没有任何反馈。这里改成任务：POST 立刻返回，前端按 stage/done/total 显示进度，
    完成后再去下载。
    """
    now = time.time()
    _prune_export_jobs(now)
    running = sum(1 for job in _EXPORT_JOBS.values() if job["state"] == "running")
    if running >= _EXPORT_MAX_RUNNING:
        raise HTTPException(status_code=429, detail="已有一个导出任务在跑，请稍候")

    job_id = uuid.uuid4().hex
    _EXPORT_JOBS[job_id] = {
        "state": "running",
        "stage": "collecting",
        "done": 0,
        "total": 0,
        "unit": "count",
        "error": "",
        "started_at": now,
        "path": None,
        "name": "",
        "bytes": 0,
        "temp_paths": [],
    }
    asyncio.create_task(_run_export_job(job_id, payload, db))
    return {"job_id": job_id, "state": "running"}


@router.get("/export/jobs/{job_id}")
async def read_export_backup(job_id: str) -> Dict[str, Any]:
    """导出任务进度：state + stage + done/total。"""
    job = _EXPORT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导出任务不存在或已过期")
    return _job_public_state(job)


@router.get("/export/jobs/{job_id}/download")
async def download_export_backup(job_id: str) -> FileResponse:
    """下载已完成的导出包。包同时留在 backup_exports 里作为本机备份点。"""
    job = _EXPORT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导出任务不存在或已过期")
    if job["state"] == "failed":
        raise HTTPException(status_code=409, detail=job["error"] or "导出失败")
    if job["state"] != "done":
        raise HTTPException(status_code=409, detail="还在打包，请稍候")
    path = job.get("path")
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=410, detail="导出文件已被清理，请重新导出")
    filename = job.get("name") or "luyun_backup.luyunbak"
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export")
async def export_backup(
    payload: BackupExportIn,
    db: DatabaseManager = Depends(get_db),
    session_id: str = Depends(require_session),
):
    """同步导出口令加密备份包，并在本机登记为一条导出备份点。

    兼容入口：管理后台走 ``/export/jobs`` 任务式（能看到进度、内存更省）。这条路径
    要把成品整包读回内存当响应体，只适合小包或脚本调用。
    """
    collected: dict = {}
    try:
        collected = await _collect_export_payload(payload, db)
        photo_info = collected["photo_info"]
        consistency = backup_service.photo_consistency(
            photo_info.get("manifest") or {},
            photo_info.get("missing") or {},
        )
        blob, meta = backup_service.build_export_backup(
            payload.passphrase,
            include_runtime=payload.include_runtime,
            runtime_data=collected["runtime_data"],
            include_app_db=payload.include_app_db,
            app_pg_bytes=_read_temp_bytes(collected["app_pg_path"]),
            include_standard_photos=payload.include_standard_photos,
            include_other_photos=payload.include_other_photos,
            photo_members={
                name: path.read_bytes()
                for name, path in (photo_info.get("members") or [])
            },
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
    finally:
        _cleanup_temp_files(collected.get("temp_paths") or [])

    ts = datetime.now(credentials_store.CHINA_TZ).strftime("%Y%m%d_%H%M%S")
    filename = f"luyun_backup_{ts}.luyunbak"
    export_dir = backup_service._export_root()
    export_dir.mkdir(parents=True, exist_ok=True)
    archive_path = export_dir / filename
    try:
        archive_path.write_bytes(blob)
    except OSError as exc:
        logger.error("写本机导出副本失败: %s", exc)
    else:
        await _register_export_archive(archive_path, meta, db)

    logger.info(
        "📦 [审计] 导出系统备份（runtime=%s app_db=%s 整库 dump=%s 标准图=%s 其它照片=%s）",
        payload.include_runtime,
        payload.include_app_db,
        bool(collected.get("app_pg_path")),
        payload.include_standard_photos,
        payload.include_other_photos,
    )
    # 本机新增了一个导出备份点：让健康结论下次读取时重算，别和列表打架。
    backup_points.invalidate_health_cache()

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


async def _after_restore_photo_consistency(
    touched: bool, db: DatabaseManager
) -> Optional[dict]:
    """恢复后核对「库引用的照片」是否真的在磁盘上。

    库和照片是两份数据，恢复时可以只换其中一份（旧归档不带照片、只勾选恢复
    业务数据、跨机搬运整库 dump 都会如此）。这里把恢复结果落成结论，缺图不再
    静默成功。``touched`` 为假（本次没有写库也没写照片）时不做检查。
    """
    if not touched:
        return None
    result = await backup_service.missing_hygiene_capture_ids(db)
    if not result["ok"]:
        logger.warning(
            "⚠️ [审计] 恢复后照片不一致：标准图缺 %s 张、其它照片缺 %s 张"
            "（库已引用但磁盘上找不到，标准图清单会缺这些项，"
            "请重新上传标准图或从冷备归档补齐）",
            result["standard_missing"],
            result["other_missing"],
        )
    return result


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
    validation = await _validation(parsed, db)
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

    # 不兼容 / 不可恢复的组合必须在**建前置快照之前**判掉：留到应用阶段才发现，
    # 会白建一份快照，而那时快照里已经含了刚写进去的凭据/运行配置。
    app_db_bytes = parsed["app_db_bytes"]
    app_pg_bytes = parsed.get("app_pg_bytes")

    if apply_app_db and app_db_bytes is not None and app_pg_bytes is None:
        # 不静默跳过：SQLite 时代的包（业务数据成员是 app.db）灌不进 PostgreSQL。
        raise HTTPException(
            status_code=400,
            detail=(
                "这份备份里的业务数据是 SQLite 库（app.db），不能直接灌进 PostgreSQL。"
                "请改用业务数据成员是 app.pgdump 的备份（当前后端导出的包即是），"
                "或在「备份中心 → 备份点 → 本机回滚快照」用「恢复整库数据」"
            ),
        )
    if apply_app_db and app_pg_bytes is not None and mode != "overwrite":
        # pg_restore --clean 是库级操作，没有逐表合并的粒度
        raise HTTPException(
            status_code=400,
            detail=(
                "PostgreSQL 的业务数据只能整库覆盖恢复（pg_restore --clean），"
                "不支持合并导入。请把恢复模式改成「覆盖」后重试"
            ),
        )
    if apply_recipes and not (apply_app_db and app_pg_bytes is not None):
        # 配方表在业务库里，没有独立的恢复路径：只勾配方、不恢复业务数据什么也不会发生，
        # 静默跳过等于骗调用方。
        raise HTTPException(
            status_code=400,
            detail=(
                "配方数据随业务数据（整库 app.pgdump）一起恢复，没有单独的配方成员。"
                "请改为勾选「业务数据」"
            ),
        )

    will_touch_db = apply_app_db and app_pg_bytes is not None
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

    # 逐表合并报告只属于 SQLite 的逐表合并路径；整库恢复没有「部分成功」，
    # 保留空 dict 是为了不改动响应结构。
    merge_reports: Dict[str, dict] = {}

    if apply_app_db and app_pg_bytes is not None:
        # 与「本机回滚快照 → 恢复整库数据」同一条路径：断连 → pg_restore --clean
        # → 连回来（含 identity 序列重置）。
        try:
            await backup_service.restore_app_pg_from_bytes(db, app_pg_bytes)
            applied[CONTENT_APP_PG] = True
        except Exception as exc:
            logger.error("应用 PostgreSQL 整库备份失败: %s", exc)
            raise HTTPException(
                status_code=500,
                detail=(
                    "应用 PostgreSQL 整库备份失败。当前库可能处于部分恢复状态，"
                    "请用恢复前自动生成的那份本机回滚快照重试"
                ),
            )

    # 整库恢复同时覆盖业务数据与配方数据：配方表与业务表在同一个库里，
    # 没有「只恢复配方」的粒度。
    if apply_recipes and applied[CONTENT_APP_PG]:
        applied[CONTENT_RECIPES] = True

    restored_photos = {PHOTO_STANDARD: 0, PHOTO_OTHER: 0}
    if apply_standard_photos or apply_other_photos:
        restored_photos = backup_points.restore_photos(
            parsed.get("standard_photos") if apply_standard_photos else {},
            parsed.get("other_photos") if apply_other_photos else {},
        )
        applied[CONTENT_STANDARD_PHOTOS] = bool(apply_standard_photos)
        applied[CONTENT_OTHER_PHOTOS] = bool(apply_other_photos)

    photo_consistency = await _after_restore_photo_consistency(
        bool(
            applied[CONTENT_APP_PG]
            or applied[CONTENT_STANDARD_PHOTOS]
            or applied[CONTENT_OTHER_PHOTOS]
        ),
        db,
    )

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

    session_invalidated = (
        applied[CONTENT_APP_PG] or applied[CONTENT_CREDENTIALS]
    )
    merge_failed_rows = sum(
        int(report.get("total_failed") or 0) for report in merge_reports.values()
    )
    return {
        "success": True,
        "mode": mode,
        "applied": applied,
        "applied_labels": [
            CONTENT_LABELS[c] for c, done in applied.items() if done
        ],
        "photos_restored": restored_photos,
        "photo_consistency": photo_consistency,
        "snapshot_ts": snapshot_ts,
        "session_invalidated": session_invalidated,
        "merge_failed_rows": merge_failed_rows,
        "merge_reports": merge_reports,
    }


@router.post("/import/preview")
async def import_backup_preview(
    file: UploadFile = File(...),
    passphrase: str = Form(...),
    session_id: str = Depends(require_session),
    db: DatabaseManager = Depends(get_db),
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
    validation = await _validation(parsed, db)
    diff = validation["cross_point"]
    will_touch = bool(
        parsed.get("app_pg_bytes")
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
        "has_sqlite_app_db": parsed["app_db_bytes"] is not None,
        "has_app_pg": parsed.get("app_pg_bytes") is not None,
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
    # 先读取、成功后再丢弃：photo_mismatch 409 时前端会带着 force 重试同一个
    # import_token，提前删暂存会让那次重试必然 404，强制继续这条安全阀就走不通。
    try:
        parsed = backup_import_staging.load_parsed_from_staging(import_token, session_id)
    except (FileNotFoundError, PermissionError, TimeoutError) as exc:
        raise _staging_http_error(exc) from exc

    result = await _apply_parsed_backup(
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
    backup_import_staging.discard_staging(import_token)
    # 恢复会写出一份前置快照备份点：健康结论需要重算。
    backup_points.invalidate_health_cache()
    return result


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
    snap_pg = snap_dir / "app.pgdump"
    if snap_pg.is_file():
        # 先断开自己的连接：pg_restore --clean 要 drop 并重建对象，我方连接上的
        # 未提交事务会持有表锁、让 drop 卡住。断开后采集侧的写入会直接失败一轮
        # （各自的错误处理会记失败计数），这是整库恢复的固有代价。
        await db.close()
        try:
            await asyncio.to_thread(backup_service.restore_pg_dump_sync, str(snap_pg))
        except Exception as exc:
            logger.error("PostgreSQL 整库恢复失败: %s", exc)
            # 尽力把连接恢复回来，让服务还能提供只读状态与再次尝试的入口
            await db.connect()
            raise HTTPException(
                status_code=500,
                detail=f"PostgreSQL 整库恢复失败：{exc}",
            ) from exc
        if not await db.connect():
            raise HTTPException(
                status_code=500,
                detail="整库已恢复，但数据库重连失败；请重启应用后再操作",
            )
        applied[CONTENT_APP_PG] = True
        # 配方表在同一个库里：整库恢复即覆盖了配方数据。
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

    photo_consistency = await _after_restore_photo_consistency(
        bool(
            applied[CONTENT_APP_PG]
            or applied[CONTENT_STANDARD_PHOTOS]
            or applied[CONTENT_OTHER_PHOTOS]
        ),
        db,
    )

    logger.info(
        "⏪ [审计] 快照回滚完成 ts=%s applied=%s pre_snapshot=%s",
        ts,
        applied,
        pre_ts,
    )
    await _notify_scraper_reload()
    await _notify_scraper_reload_runtime(db)
    # 回滚本身会新建一份前置快照备份点：健康结论需要重算。
    backup_points.invalidate_health_cache()

    return {
        "success": True,
        "ts": ts,
        "applied": applied,
        "applied_labels": [CONTENT_LABELS[c] for c, done in applied.items() if done],
        "photos_restored": restored_photos,
        "photo_consistency": photo_consistency,
        "snapshot_ts": pre_ts,
        "session_invalidated": (
            applied[CONTENT_APP_PG] or applied[CONTENT_CREDENTIALS]
        ),
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
        # 导出面板据此渲染。后端只剩 PostgreSQL（ADR 0089）：业务数据成员固定是
        # app.pgdump，只能整库覆盖恢复。
        "backend": "postgres",
        "export_app_db_supported": True,
        "app_db_export_format": "pgdump",
        "app_db_restore_mode": "overwrite_only",
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
