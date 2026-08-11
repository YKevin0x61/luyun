#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Update — Version Check seam (manifest identity + GitHub catalogue)."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import List, Optional

from services.release_update import FormalRelease, InstalledIdentity, ReleaseUpdate


@dataclass
class FakeInstalled:
    identity: InstalledIdentity

    def inspect_installed(self) -> InstalledIdentity:
        return self.identity


@dataclass
class FakeGitHub:
    releases: List[FormalRelease]

    def list_releases(self) -> List[FormalRelease]:
        return list(self.releases)

    def get_tag_commit(self, tag: str) -> Optional[str]:
        for release in self.releases:
            if release.tag == tag:
                return release.commit
        return None


def _release(
    tag: str,
    *,
    published_at: str = "2026-08-01T00:00:00Z",
    prerelease: bool = False,
    commit: Optional[str] = None,
) -> FormalRelease:
    return FormalRelease(
        tag=tag,
        name=tag.lstrip("v"),
        published_at=published_at,
        prerelease=prerelease,
        commit=commit,
    )


class VersionCheckTest(unittest.TestCase):
    def test_reports_installed_release_from_manifest(self):
        installed = FakeInstalled(
            InstalledIdentity(
                tag="v0.1.0",
                degraded=False,
                reason=None,
                commit="abc123",
            )
        )
        github = FakeGitHub(
            [
                _release("v0.2.0", commit="bbb"),
                _release("v0.1.0", published_at="2026-07-01T00:00:00Z", commit="abc123"),
            ]
        )
        result = ReleaseUpdate(
            installed=installed, github=github, app_version="0.1.0"
        ).version_check()

        self.assertEqual(result.installed_tag, "v0.1.0")
        self.assertFalse(result.degraded)
        self.assertIsNone(result.degraded_reason)
        self.assertEqual(result.app_version, "0.1.0")
        self.assertEqual([r.tag for r in result.releases], ["v0.2.0", "v0.1.0"])
        self.assertEqual(result.latest_tag, "v0.2.0")
        self.assertTrue(result.update_available)

    def test_no_update_when_manifest_already_on_latest(self):
        result = ReleaseUpdate(
            installed=FakeInstalled(
                InstalledIdentity(
                    tag="v0.2.0",
                    degraded=False,
                    reason=None,
                    commit="def456",
                )
            ),
            github=FakeGitHub(
                [
                    _release("v0.2.0", commit="def456"),
                    _release("v0.1.0", published_at="2026-07-01T00:00:00Z", commit="abc"),
                ]
            ),
            app_version="0.2.0",
        ).version_check()

        self.assertEqual(result.installed_tag, "v0.2.0")
        self.assertEqual(result.latest_tag, "v0.2.0")
        self.assertFalse(result.update_available)

    def test_no_update_when_installed_is_newer_than_catalogue_latest(self):
        result = ReleaseUpdate(
            installed=FakeInstalled(
                InstalledIdentity(
                    tag="v0.3.0",
                    degraded=False,
                    reason=None,
                    commit="fff",
                )
            ),
            github=FakeGitHub([_release("v0.2.0", commit="bbb")]),
            app_version="0.3.0",
        ).version_check()
        self.assertEqual(result.latest_tag, "v0.2.0")
        self.assertFalse(result.update_available)

    def test_missing_manifest_is_degraded_without_git_fallback(self):
        result = ReleaseUpdate(
            installed=FakeInstalled(
                InstalledIdentity(
                    tag=None,
                    degraded=True,
                    reason="missing_manifest",
                    commit=None,
                )
            ),
            github=FakeGitHub([_release("v0.2.0", commit="bbb")]),
            app_version="0.1.0",
        ).version_check()

        self.assertIsNone(result.installed_tag)
        self.assertTrue(result.degraded)
        self.assertEqual(result.degraded_reason, "missing_manifest")
        self.assertFalse(result.update_available)
        self.assertEqual(result.latest_tag, "v0.2.0")

    def test_invalid_manifest_is_degraded_at_version_check_seam(self):
        result = ReleaseUpdate(
            installed=FakeInstalled(
                InstalledIdentity(
                    tag=None,
                    degraded=True,
                    reason="invalid_manifest",
                    commit=None,
                )
            ),
            github=FakeGitHub([_release("v0.2.0", commit="bbb")]),
            app_version="0.1.0",
        ).version_check()

        self.assertTrue(result.degraded)
        self.assertEqual(result.degraded_reason, "invalid_manifest")
        self.assertFalse(result.update_available)

    def test_manifest_inconsistent_with_remote_same_tag_is_degraded(self):
        result = ReleaseUpdate(
            installed=FakeInstalled(
                InstalledIdentity(
                    tag="v0.1.0",
                    degraded=False,
                    reason=None,
                    commit="local-commit",
                )
            ),
            github=FakeGitHub(
                [
                    _release("v0.2.0", commit="newer"),
                    _release(
                        "v0.1.0",
                        published_at="2026-07-01T00:00:00Z",
                        commit="remote-commit",
                    ),
                ]
            ),
            app_version="0.1.0",
        ).version_check()

        self.assertEqual(result.installed_tag, "v0.1.0")
        self.assertTrue(result.degraded)
        self.assertEqual(result.degraded_reason, "inconsistent")
        # Still comparable for catalogue freshness; inconsistency is visible, not silent.
        self.assertTrue(result.update_available)

    def test_excludes_prereleases_from_default_catalogue(self):
        result = ReleaseUpdate(
            installed=FakeInstalled(
                InstalledIdentity(
                    tag="v0.1.0",
                    degraded=False,
                    reason=None,
                    commit="abc",
                )
            ),
            github=FakeGitHub(
                [
                    _release("v0.3.0-rc.1", published_at="2026-08-10T00:00:00Z", prerelease=True),
                    _release("v0.2.0", commit="bbb"),
                    _release("v0.1.0", published_at="2026-07-01T00:00:00Z", commit="abc"),
                ]
            ),
            app_version="0.1.0",
        ).version_check()

        self.assertEqual([r.tag for r in result.releases], ["v0.2.0", "v0.1.0"])
        self.assertEqual(result.latest_tag, "v0.2.0")
        self.assertTrue(result.update_available)
