#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release 资产下载的**重试 + 断点续传**（2026-10-10 门店更新 v0.8.4 失败那一单）。

为什么有这一组：门店更新到 v0.8.4 连着三次失败，报的是
`Remote end closed connection without response` / `[Errno 110] Connection timed out` ——
包本身在 GitHub 上完好（5.87MB、state=uploaded、digest 与本地 SHA256SUMS 一致），坏的是
「下到一半被掐断」这条链路，而当时的实现一次 `urlopen(...).read()` 失败就整单作业失败。
这里把三个契约钉死：

1. 连接被掐断 → 退避重试，且第二次带 `Range:` 从已下载的字节**续**；
2. 服务端不支持续传（回 200 而不是 206）→ 从头写，不把新旧内容拼在一起；
3. 4xx（资产不存在 / 没权限）**不重试** —— 那是确定性错误，重试只是白等。

不碰网络：`urlopen` 与 `time.sleep` 都是桩。
"""

from __future__ import annotations

import http.client
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from services.release_update.job_adapters import (
    DOWNLOAD_ATTEMPTS,
    ReleaseBundleInstallAdapter,
)


class _FakeResponse:
    """够用的 urlopen 返回物：分块吐字节，可选在读满 N 块之后抛 RemoteDisconnected。"""

    def __init__(self, chunks=None, status: int = 200, drop_after: int | None = None):
        self._chunks = list(chunks or [])
        self._drop_after = drop_after
        self._index = 0
        self.status = status

    def read(self, _size: int = -1) -> bytes:
        if self._drop_after is not None and self._index >= self._drop_after:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        if self._index >= len(self._chunks):
            return b""
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class _DownloadTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.dest = self.root / "luyun-release-bundle.tar.gz"
        self.adapter = ReleaseBundleInstallAdapter(self.root, github_repo="o/r", token=None)
        sleep_patch = mock.patch(
            "services.release_update.job_adapters.time.sleep", return_value=None
        )
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def _patch_urlopen(self, *responses):
        return mock.patch(
            "services.release_update.job_adapters.urllib.request.urlopen",
            side_effect=list(responses),
        )


class RetryAndResumeTest(_DownloadTestCase):
    def test_dropped_connection_retries_and_resumes_from_downloaded_bytes(self) -> None:
        # 第一次：吐 6 个字节之后被掐断；第二次：服务端接受续传（206）。
        first = _FakeResponse([b"hello "], drop_after=1)
        second = _FakeResponse([b"world"], status=206)
        with self._patch_urlopen(first, second) as urlopen:
            self.adapter._download_asset("v1", "bundle.tar.gz", self.dest)

        self.assertEqual(self.dest.read_bytes(), b"hello world")
        self.assertEqual(urlopen.call_count, 2)
        # 第二次必须带 Range，从已落盘的 6 字节继续 —— 这是"续传"的全部意思。
        second_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(second_request.get_header("Range"), "bytes=6-")
        # 第一次没有 Range（当时还没有任何字节）。
        self.assertIsNone(urlopen.call_args_list[0].args[0].get_header("Range"))
        self.assertEqual(self.sleep.call_count, 1)

    def test_server_ignores_range_and_returns_full_body(self) -> None:
        # 服务端回 200（不支持续传）：必须从头写，不能把新旧内容拼起来。
        first = _FakeResponse([b"stale-partial"], drop_after=1)
        second = _FakeResponse([b"fresh", b"-full"], status=200)
        with self._patch_urlopen(first, second):
            self.adapter._download_asset("v1", "bundle.tar.gz", self.dest)

        self.assertEqual(self.dest.read_bytes(), b"fresh-full")

    def test_zero_byte_body_counts_as_a_failed_attempt(self) -> None:
        empty = _FakeResponse([], status=200)
        good = _FakeResponse([b"payload"], status=200)
        with self._patch_urlopen(empty, good) as urlopen:
            self.adapter._download_asset("v1", "bundle.tar.gz", self.dest)

        self.assertEqual(self.dest.read_bytes(), b"payload")
        self.assertEqual(urlopen.call_count, 2)


class GiveUpTest(_DownloadTestCase):
    def test_all_attempts_failing_raises_with_attempt_count(self) -> None:
        responses = [
            urllib.error.URLError(TimeoutError("[Errno 110] Connection timed out"))
            for _ in range(DOWNLOAD_ATTEMPTS)
        ]
        with self._patch_urlopen(*responses) as urlopen:
            with self.assertRaises(RuntimeError) as ctx:
                self.adapter._download_asset("v1", "bundle.tar.gz", self.dest)

        self.assertIn(f"after {DOWNLOAD_ATTEMPTS} attempts", str(ctx.exception))
        self.assertIn("Connection timed out", str(ctx.exception))
        self.assertEqual(urlopen.call_count, DOWNLOAD_ATTEMPTS)
        # 退避是 4 次（最后一次失败之后不再等）。
        self.assertEqual(self.sleep.call_count, DOWNLOAD_ATTEMPTS - 1)

    def test_missing_asset_is_not_retried(self) -> None:
        missing = urllib.error.HTTPError(
            "https://example.invalid/x", 404, "Not Found", {}, None
        )
        with self._patch_urlopen(missing) as urlopen:
            with self.assertRaises(RuntimeError) as ctx:
                self.adapter._download_asset("v1", "bundle.tar.gz", self.dest)

        self.assertIn("HTTP 404", str(ctx.exception))
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(self.sleep.call_count, 0)

    def test_server_error_is_retried(self) -> None:
        boom = urllib.error.HTTPError(
            "https://example.invalid/x", 503, "Service Unavailable", {}, None
        )
        good = _FakeResponse([b"ok"], status=200)
        with self._patch_urlopen(boom, good) as urlopen:
            self.adapter._download_asset("v1", "bundle.tar.gz", self.dest)

        self.assertEqual(self.dest.read_bytes(), b"ok")
        self.assertEqual(urlopen.call_count, 2)


class DownloadBaseTest(_DownloadTestCase):
    def test_download_base_can_point_at_a_mirror(self) -> None:
        from config import settings

        original = settings.RELEASE_DOWNLOAD_BASE
        settings.RELEASE_DOWNLOAD_BASE = "https://mirror.example.com/luyun/"
        self.addCleanup(setattr, settings, "RELEASE_DOWNLOAD_BASE", original)

        with self._patch_urlopen(_FakeResponse([b"x"], status=200)) as urlopen:
            self.adapter._download_asset("v1", "bundle.tar.gz", self.dest)

        url = urlopen.call_args_list[0].args[0].full_url
        self.assertEqual(url, "https://mirror.example.com/luyun/o/r/releases/download/v1/bundle.tar.gz")
        self.assertEqual(self.adapter._download_base(), "https://mirror.example.com/luyun")

    def test_blank_base_falls_back_to_github(self) -> None:
        from config import settings

        original = settings.RELEASE_DOWNLOAD_BASE
        settings.RELEASE_DOWNLOAD_BASE = "   "
        self.addCleanup(setattr, settings, "RELEASE_DOWNLOAD_BASE", original)

        self.assertEqual(self.adapter._download_base(), "https://github.com")


if __name__ == "__main__":
    unittest.main()
