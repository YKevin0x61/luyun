#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Installed Release identity from the local Release Manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from services.release_update import InstalledIdentity

MANIFEST_NAME = "RELEASE_MANIFEST.json"
REASON_MISSING = "missing_manifest"
REASON_INVALID = "invalid_manifest"


class ReleaseManifestAdapter:
    """Read RELEASE_MANIFEST.json from the deploy directory."""

    def __init__(self, deploy_dir: Path) -> None:
        self._deploy_dir = Path(deploy_dir)

    def inspect_installed(self) -> InstalledIdentity:
        path = self._deploy_dir / MANIFEST_NAME
        if not path.is_file():
            return InstalledIdentity(
                tag=None,
                degraded=True,
                reason=REASON_MISSING,
                commit=None,
            )

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return InstalledIdentity(
                tag=None,
                degraded=True,
                reason=REASON_INVALID,
                commit=None,
            )

        if not isinstance(raw, dict):
            return InstalledIdentity(
                tag=None,
                degraded=True,
                reason=REASON_INVALID,
                commit=None,
            )

        tag = _optional_str(raw.get("tag"))
        commit = _optional_str(raw.get("commit"))
        # Tag + commit are the installed-Release identity; missing either is invalid.
        if not tag or not commit:
            return InstalledIdentity(
                tag=tag,
                degraded=True,
                reason=REASON_INVALID,
                commit=commit,
            )

        return InstalledIdentity(
            tag=tag,
            degraded=False,
            reason=None,
            commit=commit,
        )


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
