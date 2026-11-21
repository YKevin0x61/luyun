#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页版 worktool2 全量迁移：暂存上传文件、dry-run 预览、确认写库。

命令行入口仍然是 scripts/import_worktool2_purchase.py；这里只解决「管理员在网页上
上传云开发导出的 8 个集合」这一段。真正的映射与幂等规则全部复用
services/purchase/importer.py 的 import_source / ImportRunner，网页与命令行不会
出现两套口径。

任务状态只保存在本进程内（迁移是一次性、短时操作，没有跨进程恢复的必要）：
上传目录在 DATABASE_DIR/purchase-migrations/ 下，默认保留 24 小时；dry-run 与
apply 都读同一个目录，apply 失败可以直接重试，不会因为任务失败丢掉上传文件。
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import stat
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.purchase.importer import (
    COLLECTIONS,
    ImportSchemaError,
    ImportSourceError,
    discover_files,
    import_source,
)

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
MAX_FILES = 50
MAX_RETENTION_SECONDS = 24 * 60 * 60
MAX_LIMIT = 1_000_000
_ALLOWED_SUFFIXES = (".json", ".jsonl", ".ndjson", ".gz")
_READ_CHUNK = 1024 * 1024

STAGE_TEXT = {
    "queued": "排队中",
    "connecting": "连接目标库",
    "checking_schema": "检查采购表",
    "positions": "岗位",
    "suppliers": "供应商",
    "categories": "分类",
    "products": "商品",
    "date_factor_tags": "日期标签",
    "date_factor_weekday_defaults": "星期默认标签",
    "orders": "采购单",
    "material_stock": "盘点快照",
    "refresh_counters": "刷新编码计数器",
    "done": "完成",
    "failed": "失败",
}


class MigrationError(Exception):
    """网页迁移的可预期错误；code 供 HTTP 层翻译成状态码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class MigrationJob:
    id: str
    migration_id: str
    kind: str
    state: str = "queued"
    stage: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    report: Optional[Dict[str, Any]] = None


@dataclass
class Migration:
    id: str
    directory: Path
    created_at: float
    expires_at: float
    files: List[Dict[str, Any]]
    collections: Dict[str, Dict[str, Any]]
    options: Dict[str, Any]
    ignored: List[str] = field(default_factory=list)
    preview_job_id: Optional[str] = None
    apply_job_id: Optional[str] = None


def _is_allowed(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(_ALLOWED_SUFFIXES)


def _collection_for(name: str) -> Optional[str]:
    lower = name.lower()
    for collection in COLLECTIONS:
        if (
            lower == collection + ".json"
            or lower.startswith(collection + ".")
            or lower.startswith(collection + "-")
        ):
            return collection
    return None


def _public_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    """报告回前端时隐去服务器绝对路径；其余字段原样保留。"""
    cleaned = dict(payload)
    cleaned["source_dir"] = "上传文件"
    collections: Dict[str, Any] = {}
    for name, info in (payload.get("collections") or {}).items():
        item = dict(info)
        if item.get("file"):
            item["file"] = Path(str(item["file"])).name
        collections[name] = item
    cleaned["collections"] = collections
    return cleaned


def _normalise_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = options or {}
    try:
        limit = int(source.get("limit") or 0)
    except (TypeError, ValueError):
        raise MigrationError("bad_request", "limit 必须是整数")
    if limit < 0 or limit > MAX_LIMIT:
        raise MigrationError("bad_request", f"limit 必须在 0–{MAX_LIMIT} 之间")
    return {
        "sync_catalog": bool(source.get("sync_catalog")),
        "skip_orders": bool(source.get("skip_orders")),
        "skip_stock": bool(source.get("skip_stock")),
        "limit": limit,
    }


class MigrationManager:
    """进程内迁移任务管理器；同一时刻只跑一个 preview / apply。"""

    def __init__(self) -> None:
        self._items: Dict[str, Migration] = {}
        self._jobs: Dict[str, MigrationJob] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    # ---- 目录与清理 -------------------------------------------------

    def _root(self) -> Path:
        from config import settings

        return Path(settings.DATABASE_DIR) / "purchase-migrations"

    def _has_running(self, migration: Migration) -> bool:
        for job_id in (migration.preview_job_id, migration.apply_job_id):
            job = self._jobs.get(job_id or "")
            if job is not None and job.state in {"queued", "running"}:
                return True
        return False

    def _purge_expired(self) -> None:
        now = time.time()
        for migration_id, migration in list(self._items.items()):
            if migration.expires_at > now or self._has_running(migration):
                continue
            self._remove(migration_id)

    def _remove(self, migration_id: str) -> None:
        migration = self._items.pop(migration_id, None)
        if migration is None:
            return
        for job_id in (migration.preview_job_id, migration.apply_job_id):
            if job_id:
                self._jobs.pop(job_id, None)
        shutil.rmtree(migration.directory, ignore_errors=True)

    def _require(self, migration_id: str) -> Migration:
        self._purge_expired()
        migration = self._items.get(migration_id)
        if migration is None:
            raise MigrationError("not_found", "迁移记录不存在或已过期，请重新上传")
        if migration.expires_at <= time.time() and not self._has_running(migration):
            self._remove(migration_id)
            raise MigrationError("expired", "迁移记录已过期，请重新上传")
        return migration

    # ---- 上传暂存 ---------------------------------------------------

    async def stage(
        self,
        uploads: Sequence[Tuple[str, bytes]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Migration:
        if not uploads:
            raise MigrationError("no_files", "请至少上传一个集合文件或一个 zip")
        return await asyncio.to_thread(self._stage_sync, list(uploads), options)

    def _stage_sync(
        self, uploads: List[Tuple[str, bytes]], options: Optional[Dict[str, Any]]
    ) -> Migration:
        total = 0
        cleaned: List[Tuple[str, bytes]] = []
        for raw_name, data in uploads:
            name = Path(str(raw_name or "")).name
            if not name or name.startswith("."):
                raise MigrationError("bad_file", "上传文件名不合法")
            if not name.lower().endswith(".zip") and not _is_allowed(name):
                raise MigrationError(
                    "bad_file",
                    "只收 .json / .jsonl / .ndjson / .gz 或一个 .zip",
                )
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                raise MigrationError(
                    "too_large",
                    f"单次上传合计不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                )
            cleaned.append((name, data))
        if len(cleaned) > MAX_FILES:
            raise MigrationError("too_many_files", f"一次最多 {MAX_FILES} 个文件")
        zip_uploads = [item for item in cleaned if item[0].lower().endswith(".zip")]
        if zip_uploads and len(cleaned) != 1:
            raise MigrationError("bad_archive", "zip 请一次只传一个，不要再混入散文件")

        self._purge_expired()
        migration_id = uuid.uuid4().hex
        directory = self._root() / migration_id
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            logger.exception("创建采购迁移暂存目录失败")
            raise MigrationError("storage", f"服务器暂存目录不可用：{exc}") from exc

        try:
            ignored: List[str] = []
            if zip_uploads:
                ignored = self._extract_zip(zip_uploads[0][1], directory)
            else:
                seen: set[str] = set()
                for name, data in cleaned:
                    if name in seen:
                        raise MigrationError("bad_file", f"文件名重复：{name}")
                    seen.add(name)
                    (directory / name).write_bytes(data)
            files = self._list_files(directory)
            if not files:
                raise MigrationError("no_files", "上传包里没有可读取的文件")
            found = discover_files(directory)
            if not found:
                raise MigrationError(
                    "no_collections",
                    "没找到采购集合文件：文件名要以 suppliers / categories / products / "
                    "positions / orders / material_stock 等集合名开头",
                )
            counts: Dict[str, List[str]] = {}
            for path in sorted(directory.iterdir()):
                if not path.is_file():
                    continue
                collection = _collection_for(path.name)
                if collection:
                    counts.setdefault(collection, []).append(path.name)
            ambiguous = {key: value for key, value in counts.items() if len(value) > 1}
            if ambiguous:
                names = "、".join(
                    f"{key}: {' / '.join(value)}" for key, value in ambiguous.items()
                )
                raise MigrationError(
                    "ambiguous_collection",
                    f"同一个集合有多份文件，请先合并：{names}",
                )
            collections: Dict[str, Dict[str, Any]] = {}
            for collection in COLLECTIONS:
                path = found.get(collection)
                if path is None:
                    collections[collection] = {"file": None, "missing": True}
                else:
                    collections[collection] = {
                        "file": path.name,
                        "size": path.stat().st_size,
                        "missing": False,
                    }
            migration = Migration(
                id=migration_id,
                directory=directory,
                created_at=time.time(),
                expires_at=time.time() + MAX_RETENTION_SECONDS,
                files=files,
                collections=collections,
                options=_normalise_options(options),
                ignored=ignored,
            )
            self._items[migration_id] = migration
            return migration
        except MigrationError:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("采购迁移文件暂存失败")
            shutil.rmtree(directory, ignore_errors=True)
            raise MigrationError("bad_file", f"读取上传文件失败：{exc}") from exc

    def _list_files(self, directory: Path) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in sorted(directory.iterdir()):
            if path.is_file():
                rows.append({"name": path.name, "size": path.stat().st_size})
        return rows

    def _extract_zip(self, data: bytes, directory: Path) -> List[str]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as exc:
            raise MigrationError("bad_archive", "这个 zip 打不开，可能不是有效的压缩包") from exc
        ignored: List[str] = []
        total = 0
        written = 0
        seen: set[str] = set()
        try:
            with archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    raw_name = info.filename.replace("\\", "/")
                    base = Path(raw_name).name
                    if not base or base.startswith("."):
                        continue
                    if "__MACOSX" in raw_name.split("/"):
                        ignored.append(base)
                        continue
                    if info.flag_bits & 0x1:
                        raise MigrationError("bad_archive", "不支持加密的 zip，请先解压后再传")
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise MigrationError("bad_archive", f"zip 里不允许符号链接：{base}")
                    if not _is_allowed(base):
                        ignored.append(base)
                        continue
                    if base in seen:
                        raise MigrationError("bad_file", f"zip 里有重名文件：{base}")
                    seen.add(base)
                    written += 1
                    if written > MAX_FILES:
                        raise MigrationError("too_many_files", f"一次最多 {MAX_FILES} 个文件")
                    if info.file_size > MAX_UNCOMPRESSED_BYTES:
                        raise MigrationError("too_large", f"{base} 解压后超过 300 MB")
                    target = directory / base
                    size = 0
                    with archive.open(info) as source, target.open("wb") as sink:
                        while True:
                            chunk = source.read(_READ_CHUNK)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > MAX_UNCOMPRESSED_BYTES:
                                raise MigrationError("too_large", f"{base} 解压后超过 300 MB")
                            sink.write(chunk)
                    total += size
                    if total > MAX_UNCOMPRESSED_BYTES:
                        raise MigrationError("too_large", "zip 解压后的总大小超过 300 MB")
        finally:
            archive.close()
        if written == 0:
            raise MigrationError("no_files", "zip 里没有 .json / .jsonl / .ndjson / .gz 文件")
        return ignored

    # ---- 任务 -------------------------------------------------------

    def _ensure_idle(self, migration: Migration) -> None:
        if self._has_running(migration):
            raise MigrationError("busy", "已经有一个迁移任务在跑，等它结束再试")

    def _new_job(self, migration: Migration, kind: str) -> MigrationJob:
        job = MigrationJob(
            id=uuid.uuid4().hex,
            migration_id=migration.id,
            kind=kind,
        )
        self._jobs[job.id] = job
        return job

    def _launch(self, migration: Migration, job: MigrationJob, apply: bool) -> None:
        task = asyncio.create_task(self._run(migration, job, apply))
        self._tasks[job.id] = task

        def done(_task: asyncio.Task) -> None:
            self._tasks.pop(job.id, None)

        task.add_done_callback(done)

    async def _run(self, migration: Migration, job: MigrationJob, apply: bool) -> None:
        job.state = "running"
        job.stage = "connecting"
        job.started_at = time.time()
        try:
            report = await import_source(
                migration.directory,
                self._dsn(),
                apply=apply,
                sync_catalog=bool(migration.options.get("sync_catalog")),
                skip_orders=bool(migration.options.get("skip_orders")),
                skip_stock=bool(migration.options.get("skip_stock")),
                limit=int(migration.options.get("limit") or 0),
                on_stage=lambda stage: setattr(job, "stage", stage),
            )
            job.report = _public_report(report.to_dict())
            job.state = "done"
            job.stage = "done"
        except ImportSchemaError as exc:
            job.state = "failed"
            job.stage = "failed"
            job.error_code = "not_migrated"
            job.error = str(exc)
        except ImportSourceError as exc:
            job.state = "failed"
            job.stage = "failed"
            job.error_code = "bad_source"
            job.error = str(exc)
        except asyncio.CancelledError:
            job.state = "failed"
            job.stage = "failed"
            job.error_code = "cancelled"
            job.error = "任务被中断"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("采购迁移任务失败 migration=%s kind=%s", migration.id, job.kind)
            job.state = "failed"
            job.stage = "failed"
            job.error_code = "failed"
            job.error = f"迁移失败：{exc}"
        finally:
            job.finished_at = time.time()

    def _dsn(self) -> str:
        from config import settings

        return str(settings.POSTGRES_DSN)

    def start_preview(
        self, migration_id: str, options: Optional[Dict[str, Any]] = None
    ) -> MigrationJob:
        migration = self._require(migration_id)
        self._ensure_idle(migration)
        if options is not None:
            migration.options = _normalise_options(options)
        job = self._new_job(migration, "preview")
        migration.preview_job_id = job.id
        self._launch(migration, job, apply=False)
        return job

    def start_apply(self, migration_id: str) -> MigrationJob:
        migration = self._require(migration_id)
        self._ensure_idle(migration)
        preview = self._jobs.get(migration.preview_job_id or "")
        if preview is None:
            raise MigrationError("preview_required", "先跑一次 dry-run 预览，再确认导入")
        if preview.state != "done":
            raise MigrationError("preview_not_done", "预览还没成功结束，不能写库")
        job = self._new_job(migration, "apply")
        migration.apply_job_id = job.id
        self._launch(migration, job, apply=True)
        return job

    # ---- 读取 -------------------------------------------------------

    def _job_public(self, job: Optional[MigrationJob]) -> Optional[Dict[str, Any]]:
        if job is None:
            return None
        return {
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "stage": job.stage,
            "stage_text": STAGE_TEXT.get(job.stage, job.stage),
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "error_code": job.error_code,
            "report": job.report,
        }

    def public(self, migration_id: str) -> Dict[str, Any]:
        migration = self._require(migration_id)
        preview = self._jobs.get(migration.preview_job_id or "")
        apply_job = self._jobs.get(migration.apply_job_id or "")
        missing = [
            name
            for name, info in migration.collections.items()
            if info.get("missing")
        ]
        return {
            "id": migration.id,
            "created_at": migration.created_at,
            "expires_at": migration.expires_at,
            "files": migration.files,
            "ignored": migration.ignored,
            "collections": migration.collections,
            "missing": missing,
            "options": migration.options,
            "preview": self._job_public(preview),
            "apply": self._job_public(apply_job),
        }

    def job(self, migration_id: str, job_id: str) -> MigrationJob:
        self._require(migration_id)
        job = self._jobs.get(job_id)
        if job is None or job.migration_id != migration_id:
            raise MigrationError("not_found", "任务不存在")
        return job

    def job_public(self, migration_id: str, job_id: str) -> Dict[str, Any]:
        result = self._job_public(self.job(migration_id, job_id))
        assert result is not None
        return result

    def discard(self, migration_id: str) -> None:
        migration = self._require(migration_id)
        if self._has_running(migration):
            raise MigrationError("busy", "任务还在跑，等它结束再丢弃")
        self._remove(migration_id)

    def reset_for_tests(self) -> None:
        """只给测试用：清掉进程内状态与暂存目录。"""
        for migration in list(self._items.values()):
            shutil.rmtree(migration.directory, ignore_errors=True)
        self._items.clear()
        self._jobs.clear()
        self._tasks.clear()


migration_manager = MigrationManager()
