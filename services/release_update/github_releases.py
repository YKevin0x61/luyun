#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Releases API adapter (fine-grained read-only PAT)."""

from __future__ import annotations

from typing import List, Optional, Sequence

import httpx

from services.release_update import FormalRelease


class GitHubReleasesError(RuntimeError):
    """Raised when the Releases API cannot be queried."""


class GitHubReleasesAdapter:
    """List releases for owner/repo via the GitHub REST API."""

    def __init__(
        self,
        *,
        repo: str,
        token: Optional[str],
        api_base: str = "https://api.github.com",
        timeout_s: float = 15.0,
    ) -> None:
        self._repo = (repo or "").strip()
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._timeout_s = timeout_s

    def _require_repo(self) -> None:
        if "/" not in self._repo:
            raise GitHubReleasesError(
                "GITHUB_REPO is not configured (expected owner/name)"
            )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "luyun-release-update",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def list_releases(self) -> Sequence[FormalRelease]:
        """Return the raw Releases API list (including prereleases).

        Default formal-catalogue filtering lives in ReleaseUpdate.version_check.
        """
        self._require_repo()
        headers = self._headers()

        url = f"{self._api_base}/repos/{self._repo}/releases"
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.get(url, headers=headers, params={"per_page": 30})
        except httpx.HTTPError as exc:
            raise GitHubReleasesError(f"GitHub Releases request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise GitHubReleasesError(
                f"GitHub Releases API HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise GitHubReleasesError("GitHub Releases response was not JSON") from exc

        if not isinstance(payload, list):
            raise GitHubReleasesError("GitHub Releases response was not a list")

        releases: List[FormalRelease] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag_name")
            if not tag:
                continue
            releases.append(
                FormalRelease(
                    tag=str(tag),
                    name=str(item.get("name") or tag),
                    published_at=str(item.get("published_at") or ""),
                    prerelease=bool(item.get("prerelease")),
                )
            )
        return releases

    def get_tag_commit(self, tag: str) -> Optional[str]:
        """Resolve a Release tag to its commit SHA (for manifest consistency)."""
        tag = (tag or "").strip()
        if not tag:
            return None
        self._require_repo()
        headers = self._headers()

        # Prefer the annotated/lightweight tag object; peel to commit when needed.
        ref_url = f"{self._api_base}/repos/{self._repo}/git/ref/tags/{tag}"
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.get(ref_url, headers=headers)
                if resp.status_code == 404:
                    return None
                if resp.status_code >= 400:
                    raise GitHubReleasesError(
                        f"GitHub tag ref API HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                try:
                    ref_payload = resp.json()
                except ValueError as exc:
                    raise GitHubReleasesError(
                        "GitHub tag ref response was not JSON"
                    ) from exc
                if not isinstance(ref_payload, dict):
                    return None
                obj = ref_payload.get("object") or {}
                if not isinstance(obj, dict):
                    return None
                obj_type = str(obj.get("type") or "")
                sha = str(obj.get("sha") or "").strip() or None
                if not sha:
                    return None
                if obj_type == "commit":
                    return sha
                if obj_type != "tag":
                    return sha
                tag_url = f"{self._api_base}/repos/{self._repo}/git/tags/{sha}"
                tag_resp = client.get(tag_url, headers=headers)
                if tag_resp.status_code == 404:
                    return None
                if tag_resp.status_code >= 400:
                    raise GitHubReleasesError(
                        f"GitHub tag object API HTTP {tag_resp.status_code}: "
                        f"{tag_resp.text[:200]}"
                    )
                try:
                    tag_payload = tag_resp.json()
                except ValueError as exc:
                    raise GitHubReleasesError(
                        "GitHub tag object response was not JSON"
                    ) from exc
        except httpx.HTTPError as exc:
            raise GitHubReleasesError(
                f"GitHub tag commit resolve failed: {exc}"
            ) from exc

        if not isinstance(tag_payload, dict):
            return None
        peeled = tag_payload.get("object") or {}
        if not isinstance(peeled, dict):
            return None
        return str(peeled.get("sha") or "").strip() or None
