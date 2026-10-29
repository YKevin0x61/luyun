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
    _DownloadRateMeter,
    _response_total_bytes,
)


class _FakeResponse:
    """够用的 urlopen 返回物：分块吐字节，可选在读满 N 块之后抛 RemoteDisconnected。"""

    def __init__(
        self,
        chunks=None,
        status: int = 200,
        drop_after: int | None = None,
        headers: dict | None = None,
    ):
        self._chunks = list(chunks or [])
        self._drop_after = drop_after
        self._index = 0
        self.status = status
        # 下载进度要读 Content-Length / Content-Range；不传就是「服务端没给」。
        self.headers = dict(headers or {})

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


class DownloadProgressTest(_DownloadTestCase):
    """下载进度：界面要能看出「下到哪、多快」，而不是一直一个阶段名。"""

    def _adapter_with(self, events):
        self.adapter = ReleaseBundleInstallAdapter(
            self.root,
            github_repo="o/r",
            token=None,
            on_download_progress=events.append,
        )
        return self.adapter

    def test_reports_done_and_total_from_content_length(self):
        events = []
        adapter = self._adapter_with(events)
        response = _FakeResponse(
            [b"ab", b"cd", b"ef"], status=200, headers={"Content-Length": "6"}
        )
        with self._patch_urlopen(response):
            adapter._download_asset(
                "v1", "bundle.tar.gz", self.dest, report_progress=True
            )

        self.assertTrue(events, "下载过程中至少要上报一次进度")
        last = events[-1]
        self.assertEqual(last["asset"], "bundle.tar.gz")
        self.assertEqual(last["attempt"], 1)
        self.assertEqual(last["done_bytes"], 6)
        self.assertEqual(last["total_bytes"], 6)
        self.assertIn("updated_at", last)
        # 只有一次样点时算不出速率，允许 None；给了就必须是非负数。
        rate = last["bytes_per_second"]
        self.assertTrue(rate is None or rate >= 0)

    def test_resumed_download_counts_total_from_content_range(self):
        # 已经落了 6 字节，服务端回 206：本次只传 4 字节，但总量是 10。
        self.dest.write_bytes(b"hello ")
        events = []
        adapter = self._adapter_with(events)
        response = _FakeResponse(
            [b"world"],
            status=206,
            headers={"Content-Length": "5", "Content-Range": "bytes 6-10/11"},
        )
        with self._patch_urlopen(response):
            adapter._download_asset(
                "v1", "bundle.tar.gz", self.dest, report_progress=True
            )

        last = events[-1]
        self.assertEqual(last["done_bytes"], 11)
        self.assertEqual(last["total_bytes"], 11)

    def test_progress_is_off_by_default(self):
        # SHA256SUMS 这类小文件不报进度（否则界面会为几百字节闪一次 0%→100%）。
        events = []
        adapter = self._adapter_with(events)
        with self._patch_urlopen(_FakeResponse([b"x"], status=200)):
            adapter._download_asset("v1", "SHA256SUMS", self.dest)

        self.assertEqual(events, [])

    def test_progress_callback_failure_never_breaks_the_download(self):
        def boom(_info):
            raise OSError("state file is locked")

        self.adapter = ReleaseBundleInstallAdapter(
            self.root, github_repo="o/r", token=None, on_download_progress=boom
        )
        response = _FakeResponse([b"payload"], status=200, headers={"Content-Length": "7"})
        with self._patch_urlopen(response):
            self.adapter._download_asset(
                "v1", "bundle.tar.gz", self.dest, report_progress=True
            )

        self.assertEqual(self.dest.read_bytes(), b"payload")

    def test_reports_a_retry_when_the_first_attempt_is_dropped(self):
        events = []
        adapter = self._adapter_with(events)
        first = _FakeResponse(
            [b"hello "], drop_after=1, headers={"Content-Length": "11"}
        )
        second = _FakeResponse(
            [b"world"],
            status=206,
            headers={"Content-Length": "5", "Content-Range": "bytes 6-10/11"},
        )
        with self._patch_urlopen(first, second):
            adapter._download_asset(
                "v1", "bundle.tar.gz", self.dest, report_progress=True
            )

        attempts = sorted({event["attempt"] for event in events})
        self.assertEqual(attempts, [1, 2])


class DownloadProgressHelpersTest(unittest.TestCase):
    def test_rate_meter_uses_a_rolling_window(self):
        metre = _DownloadRateMeter(window_seconds=3.0)
        metre.observe(0.0, 0)
        metre.observe(1.0, 1000)
        self.assertAlmostEqual(metre.rate(), 1000.0)

        # 4 秒后：窗口里只保留最近的样点，速率仍按 1000 B/s。
        metre.observe(4.0, 4000)
        self.assertAlmostEqual(metre.rate(), 1000.0)

        # 时间不前进算不出速率，不能除零给一个假值。
        stalled = _DownloadRateMeter()
        stalled.observe(1.0, 100)
        stalled.observe(1.0, 200)
        self.assertIsNone(stalled.rate())

    def test_total_bytes_prefers_content_range_over_length(self):
        resume = _FakeResponse(
            status=206,
            headers={"Content-Length": "5", "Content-Range": "bytes 6-10/11"},
        )
        self.assertEqual(_response_total_bytes(resume, resume_offset=6), 11)

    def test_total_bytes_falls_back_to_length_plus_resume_offset(self):
        resume = _FakeResponse(status=206, headers={"Content-Length": "5"})
        self.assertEqual(_response_total_bytes(resume, resume_offset=6), 11)

    def test_total_bytes_is_none_without_headers(self):
        self.assertIsNone(_response_total_bytes(_FakeResponse(), resume_offset=0))
        bad = _FakeResponse(headers={"Content-Length": "not-a-number"})
        self.assertIsNone(_response_total_bytes(bad, resume_offset=0))


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
