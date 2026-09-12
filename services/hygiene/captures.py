#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capture bytes live on disk, referenced from app.db. Not SQLite WAL blobs."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

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

    def get(self, capture_id: str) -> bytes:
        return self.blobs[capture_id]


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

    def get(self, capture_id: str) -> bytes:
        if not _CAPTURE_ID_RE.fullmatch(capture_id or ""):
            raise FileNotFoundError(capture_id)
        path = (self._root / capture_id).resolve()
        root = self._root.resolve()
        if not path.is_relative_to(root):
            raise FileNotFoundError(capture_id)
        return path.read_bytes()
