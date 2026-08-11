#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Git adapter: installed Release identity from the deploy directory."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Sequence

from services.release_update import InstalledIdentity


class GitIdentityAdapter:
    """Inspect exact tag / dirty / detached state via git CLI."""

    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = Path(repo_dir)

    def inspect_installed(self) -> InstalledIdentity:
        commit = self._run_text(["rev-parse", "HEAD"])
        if commit is None:
            return InstalledIdentity(
                tag=None,
                degraded=True,
                reason="git_unavailable",
                commit=None,
            )

        dirty = self._is_dirty()
        tag = self._exact_tag()
        # Prefer not_on_tag when HEAD is not an exact tag (even if dirty).
        # dirty applies only when HEAD is on a tag but the worktree is unclean.
        if tag is None:
            return InstalledIdentity(
                tag=None,
                degraded=True,
                reason="not_on_tag",
                commit=commit,
            )
        if dirty:
            return InstalledIdentity(
                tag=tag,
                degraded=True,
                reason="dirty",
                commit=commit,
            )
        return InstalledIdentity(
            tag=tag,
            degraded=False,
            reason=None,
            commit=commit,
        )

    def _exact_tag(self) -> Optional[str]:
        return self._run_text(["describe", "--exact-match", "--tags", "HEAD"])

    def _is_dirty(self) -> bool:
        out = self._run_text(["status", "--porcelain"])
        return bool(out and out.strip())

    def _run_text(self, args: Sequence[str]) -> Optional[str]:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(self._repo_dir),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return (completed.stdout or "").strip() or None
