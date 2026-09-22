#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""python-multipart 版本下界与反代请求体上限的回归（PERF-02）。

背景：``python-multipart==0.0.6`` 命中 CVE-2024-24762 —— 解析 multipart 的
``Content-Type`` 时正则灾难性回溯，占住主事件循环；本项目强制单 uvicorn
worker，事件循环被占住就是整站停摆（``/api/healthz`` 也不响应）。修复版 0.0.7。
同一个解析器后来又出了 8 条公告（落在 0.0.18 / 0.0.22 / 0.0.26 / 0.0.27 /
0.0.30 / 0.0.31 六档，见 ``KNOWN_ADVISORY_FIXES``），所以下界取最高的一档，一次覆盖
CVE-2024-24762 及其后的全部修复；停在低版本的环境不会因为约束放宽而自动再升
（pip 默认 only-if-needed），下界本身就必须抬到位。

两条不许回退的约束：

1. ``requirements.txt`` 里 python-multipart 的下界不得低于 ``KNOWN_ADVISORY_FIXES``
   的最高修复版本，且不能退回 ``==`` 钉死单版本（补丁版要能自动跟进）。下界只认
   文件里那一处，测试不再另存一份「当前下界」，避免两处漂移。
2. 反代必须给请求体加上限——应用侧 ``_read_upload_bounded`` 的体积闸门在
   **multipart 解析之后**才跑，挡不住解析期的 CPU 消耗；但上限不能小到挡掉合法
   上传：``/api/hygiene/staff/deep-clean/{item_id}/submit`` 一次要传 before + after
   两张（各 ``MAX_STANDARD_BYTES``），备份恢复还要传整包 ``.luyunbak``
   （整库 pg_dump + 凭据 + 配方 + 卫生照片，几十~几百 MB）。票面建议的统一
   8MB 会 413 掉前者，故改成「业务 64MiB + 备份导入 512MiB」两级
   （理由见 ``deploy/Caddyfile`` 注释）。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from services.hygiene.work import MAX_STANDARD_BYTES

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO_ROOT / "requirements.txt"
CADDYFILE = REPO_ROOT / "deploy" / "Caddyfile"

# go-humanize 的单位（Caddy 的 request_body max_size 用它解析；MB/MiB 不等价）
_SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
}
_NGINX_EQUIVALENT_BYTES = 512 * 1024 * 1024  # deploy/nginx.conf 的 client_max_body_size 512m

# python-multipart 已知安全公告的修复落点（版本元组）。
# 来源：GitHub Advisory Database（api.github.com/advisories?affects=python-multipart，
# 9 条）与 Debian security tracker，2026-09 核对；0.0.31 之后只有性能改进、无新公告。
# 下界必须 ≥ 这里的最高一档，否则「一次覆盖全部 multipart DoS 修复」不成立。
KNOWN_ADVISORY_FIXES = {
    "CVE-2024-24762": (0, 0, 7),
    "CVE-2024-53981": (0, 0, 18),
    "CVE-2026-24486": (0, 0, 22),
    "CVE-2026-40347": (0, 0, 26),
    "CVE-2026-42561": (0, 0, 27),
    "CVE-2026-53537": (0, 0, 30),
    "CVE-2026-53538": (0, 0, 30),
    "CVE-2026-53539": (0, 0, 30),
    "CVE-2026-53540": (0, 0, 31),
}


def _multipart_specifier() -> str:
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("python-multipart"):
            return stripped.split("#", 1)[0].strip()
    raise AssertionError("requirements.txt 里找不到 python-multipart")


def _lower_bound(specifier: str) -> tuple:
    match = re.search(r">=\s*([0-9][0-9.]*)", specifier)
    if not match:
        raise AssertionError(f"python-multipart 缺下界约束: {specifier!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def _handle_block(caddyfile: str, matcher: str) -> str:
    """取出 ``handle <matcher> { ... }`` 的块体（含 request_body 的嵌套花括号）。"""
    start = caddyfile.index(f"handle {matcher} {{")
    depth = 0
    for index in range(start, len(caddyfile)):
        char = caddyfile[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return caddyfile[start : index + 1]
    raise AssertionError(f"Caddyfile 的 {matcher} handle 块没有闭合")


def _max_size_bytes(block: str) -> int:
    match = re.search(r"max_size\s+([0-9]+)\s*([A-Za-z]+)", block)
    if not match:
        raise AssertionError(f"handle 块里没有 request_body max_size: {block!r}")
    unit = match.group(2).upper()
    if unit not in _SIZE_UNITS:
        raise AssertionError(f"认不出的体积单位: {match.group(2)!r}")
    return int(match.group(1)) * _SIZE_UNITS[unit]


class MultipartDependencyFloorTest(unittest.TestCase):
    def test_floor_covers_every_known_advisory(self):
        specifier = _multipart_specifier()
        required = max(KNOWN_ADVISORY_FIXES.values())
        covered = " / ".join(
            cve for cve, fixed in KNOWN_ADVISORY_FIXES.items() if fixed == required
        )
        self.assertGreaterEqual(
            _lower_bound(specifier),
            required,
            "python-multipart 下界必须 ≥ "
            f"{'.'.join(str(part) for part in required)}（已知公告里最高的一档修复"
            f"版本，覆盖 {covered}），否则停在 0.0.18~0.0.30 的环境不会因为约束"
            f"放宽而自动再升（pip 默认 only-if-needed），当前: {specifier!r}",
        )

    def test_not_pinned_to_a_single_vulnerable_version(self):
        specifier = _multipart_specifier()
        self.assertIsNone(
            re.match(r"python-multipart==", specifier),
            f"不要再钉死单版本，否则补丁版无法自动跟进: {specifier!r}",
        )


class ReverseProxyBodyLimitContractTest(unittest.TestCase):
    def test_api_handle_caps_bodies_above_the_two_photo_upload(self):
        block = _handle_block(CADDYFILE.read_text(encoding="utf-8"), "@api")
        self.assertGreaterEqual(
            _max_size_bytes(block),
            2 * MAX_STANDARD_BYTES,
            "业务上限必须容得下 deep-clean 一次提交的 before + after 两张标准图"
            f"（各 {MAX_STANDARD_BYTES} 字节）",
        )

    def test_backup_import_has_its_own_larger_limit(self):
        caddyfile = CADDYFILE.read_text(encoding="utf-8")
        api_limit = _max_size_bytes(_handle_block(caddyfile, "@api"))
        backup_limit = _max_size_bytes(_handle_block(caddyfile, "@backup_import"))
        self.assertGreater(
            backup_limit,
            api_limit,
            "备份恢复传的是整包 .luyunbak，不能套业务接口的上限",
        )
        self.assertGreaterEqual(
            backup_limit,
            _NGINX_EQUIVALENT_BYTES,
            "备份上限不得低于 deploy/nginx.conf 的 client_max_body_size 512m，"
            "否则两套反代行为不一致（同一份备份走 Caddy 会 413、走 Nginx 能过）",
        )

    def test_backup_handle_is_declared_before_the_api_handle(self):
        caddyfile = CADDYFILE.read_text(encoding="utf-8")
        self.assertLess(
            caddyfile.index("handle @backup_import"),
            caddyfile.index("handle @api"),
            "Caddy 的 handle 块按声明顺序匹配、第一个命中的生效，"
            "大体积那条必须排在 @api 之前",
        )


if __name__ == "__main__":
    unittest.main()
