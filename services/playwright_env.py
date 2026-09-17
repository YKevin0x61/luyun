#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Playwright 浏览器二进制与 lib 版本的一致性保障。

背景
----
``playwright`` 的 Python 包与浏览器 build 强绑定：lib 1.63.0 只认
``/ms-playwright/chromium-1243`` 与 ``chromium_headless_shell-1243``。
两者分别在不同时间点确定时就会漂移：

- Docker 镜像构建期用 system pip 的 playwright 预装浏览器；
- 运行期 uvicorn 走 ``.venv``，其 playwright 版本由当时的 requirements 解析决定。

漂移的后果是 scraper 报
``BrowserType.launch: Executable doesn't exist at /ms-playwright/chromium_headless_shell-1243/...``——
真正的缺失是浏览器，不是代码路径。这里把「判断缺失」与「补装浏览器」收敛到
一处，供三处复用：

1. 应用层：``PosSession._init_browser`` launch 失败后的自愈重试；
2. 更新作业：``pip sync`` 之后同步浏览器（``PipDepsSyncAdapter`` 之后的步骤）；
3. Docker entrypoint：容器启动前的健康检查（shell 侧调用同一命令）。

``playwright install chromium`` 自 1.49 起会同时安装 ``chromium`` 与
``chromium_headless_shell``，headless 启动用的正是后者，因此只需这一条命令。
反复执行是幂等的：目标 build 已存在时秒退。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# Playwright 在浏览器缺失时给出的固定措辞（异步/同步 API 一致）。
_BROWSER_MISSING_MARKERS = (
    "executable doesn't exist",
    "executable does not exist",
    "looks like playwright was just installed or updated",
)

_install_lock = asyncio.Lock()


def is_browser_missing_error(exc: "BaseException | str") -> bool:
    """异常是否表示「浏览器二进制缺失」（而非磁盘满、权限、网络等其它原因）。"""
    message = str(exc).lower()
    return any(marker in message for marker in _BROWSER_MISSING_MARKERS)


def browsers_path() -> Optional[str]:
    """当前生效的 PLAYWRIGHT_BROWSERS_PATH（None 表示用 Playwright 默认目录）。"""
    import os

    return os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or None


def _install_command(python_bin: str) -> list[str]:
    # 不带 --with-deps：系统依赖由镜像/宿主 bootstrap 装好，这里只补二进制，
    # 保证在容器内以非 root 也能跑通且够快。
    return [python_bin, "-m", "playwright", "install", "chromium"]


def _timeout() -> int:
    return max(60, int(settings.PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS))


def ensure_chromium_installed_sync(
    *,
    python_bin: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    log: Optional[logging.Logger] = None,
) -> bool:
    """同步补装 Chromium，返回是否成功。给 subprocess 编排（更新作业）使用。"""
    log = log or logger
    python_bin = python_bin or sys.executable
    timeout = timeout_seconds or _timeout()
    cmd = _install_command(python_bin)
    log.info("Playwright 浏览器缺失，执行: %s", " ".join(cmd))
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("Playwright 浏览器补装失败: %s", exc)
        return False
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        log.error("Playwright 浏览器补装失败 exit=%s: %s", completed.returncode, tail)
        return False
    log.info("Playwright 浏览器补装完成（browsers_path=%s）", browsers_path())
    return True


async def ensure_chromium_installed(
    *,
    python_bin: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    log: Optional[logging.Logger] = None,
) -> bool:
    """异步补装 Chromium，返回是否成功。

    并发调用串行执行（第二次通常秒退，因为 build 已存在），避免同一进程里
    多个任务同时下载同一个浏览器。
    """
    log = log or logger
    async with _install_lock:
        python_bin = python_bin or sys.executable
        timeout = timeout_seconds or _timeout()
        cmd = _install_command(python_bin)
        log.warning("Playwright 浏览器缺失，执行: %s", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            log.error("Playwright 浏览器补装失败: %s", exc)
            return False
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            log.error("Playwright 浏览器补装超时（%ss）", timeout)
            return False
        output = (stdout or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            log.error(
                "Playwright 浏览器补装失败 exit=%s: %s",
                proc.returncode,
                output[-2000:],
            )
            return False
        log.info("Playwright 浏览器补装完成（browsers_path=%s）", browsers_path())
        return True
