#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capture bytes live on disk, referenced from app.db. Not SQLite WAL blobs."""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from pathlib import Path

import aiofiles

_CAPTURE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class FakeCaptureStore:
    """In-memory store for tests. Production uses FileCaptureStore under data/."""

    def __init__(self):
        self.blobs = {}
        self._n = 0

    def put(self, data: bytes, content_type: str = "image/jpeg") -> str:
        del content_type
        self._n += 1
        capture_id = f"fake-{self._n}"
        self.blobs[capture_id] = data
        return capture_id

    async def put_async(self, data: bytes, content_type: str = "image/jpeg") -> str:
        return self.put(data, content_type)

    def get(self, capture_id: str) -> bytes:
        try:
            return self.blobs[capture_id]
        except KeyError as exc:
            raise FileNotFoundError(capture_id) from exc

    async def get_async(self, capture_id: str) -> bytes:
        return self.get(capture_id)

    def exists(self, capture_id: str) -> bool:
        return capture_id in self.blobs

    async def exists_async(self, capture_id: str) -> bool:
        return self.exists(capture_id)

    async def delete_async(self, capture_id: str) -> None:
        self.blobs.pop(capture_id, None)

    async def path_async(self, capture_id: str):
        del capture_id
        return None

    async def list_ids_async(self) -> list[str]:
        return list(self.blobs)

    async def modified_at_async(self, capture_id: str):
        return time.time() if capture_id in self.blobs else None


class FileCaptureStore:
    def __init__(self, root):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, content_type: str = "image/jpeg") -> str:
        del content_type  # persisted on hygiene_standards, not in the filename
        capture_id = uuid.uuid4().hex
        path = self._root / capture_id
        path.write_bytes(data)
        return capture_id

    async def put_async(self, data: bytes, content_type: str = "image/jpeg") -> str:
        del content_type
        capture_id = uuid.uuid4().hex
        path = self._root / capture_id
        tmp_path = self._root / f".{capture_id}.tmp"
        try:
            async with aiofiles.open(tmp_path, "wb") as handle:
                await handle.write(data)
                await handle.flush()
                await asyncio.to_thread(os.fsync, handle.fileno())
            await asyncio.to_thread(os.replace, tmp_path, path)
        except Exception:
            try:
                await asyncio.to_thread(tmp_path.unlink)
            except FileNotFoundError:
                pass
            raise
        return capture_id

    def _path(self, capture_id: str) -> Path:
        if not _CAPTURE_ID_RE.fullmatch(capture_id or ""):
            raise FileNotFoundError(capture_id)
        path = (self._root / capture_id).resolve()
        root = self._root.resolve()
        if not path.is_relative_to(root):
            raise FileNotFoundError(capture_id)
        return path

    def get(self, capture_id: str) -> bytes:
        return self._path(capture_id).read_bytes()

    async def get_async(self, capture_id: str) -> bytes:
        path = self._path(capture_id)
        async with aiofiles.open(path, "rb") as handle:
            return await handle.read()

    def exists(self, capture_id: str) -> bool:
        try:
            return self._path(capture_id).is_file()
        except FileNotFoundError:
            return False

    async def exists_async(self, capture_id: str) -> bool:
        try:
            path = self._path(capture_id)
        except FileNotFoundError:
            return False
        return await asyncio.to_thread(path.is_file)

    async def delete_async(self, capture_id: str) -> None:
        try:
            path = self._path(capture_id)
        except FileNotFoundError:
            return
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return

    async def path_async(self, capture_id: str):
        try:
            path = self._path(capture_id)
        except FileNotFoundError:
            return None
        return path if await asyncio.to_thread(path.is_file) else None

    async def list_ids_async(self) -> list[str]:
        def scan() -> list[str]:
            return sorted(
                entry.name
                for entry in self._root.iterdir()
                if entry.is_file() and _CAPTURE_ID_RE.fullmatch(entry.name)
            )

        return await asyncio.to_thread(scan)

    async def modified_at_async(self, capture_id: str):
        try:
            path = self._path(capture_id)
        except FileNotFoundError:
            return None
        try:
            stat = await asyncio.to_thread(path.stat)
        except FileNotFoundError:
            return None
        return stat.st_mtime
