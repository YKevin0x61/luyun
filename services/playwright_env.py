#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Playwright 浏览器二进制与 lib 版本的一致性保障。

背景
----
``playwright`` 的 Python 包与浏览器 build 强绑定：**每个 lib 版本都自带一张
「期望 build」表**，就在 ``playwright/driver/package/browsers.json``——playwright
自己启动浏览器时读的就是这个文件。**任何地方都不要再手抄这份映射**：
``requirements.txt`` 曾把「1.63.0 ↔ chromium 1243」写成注释，lib 一升就漂移。
运行期读 lib 自带的 ``browsers.json`` 才是唯一事实来源（见
:func:`expected_browser_builds`），命令行守卫 ``python -m services.playwright_env
--check`` 用它离线校验缓存里到底有没有目标 build。

漂移的后果是 scraper 报
``BrowserType.launch: Executable doesn't exist at .../chromium_headless_shell-<rev>/...``
——真正的缺失是浏览器，不是代码路径。这里把「判断缺失」「校验一致性」与
「补装浏览器」收敛到一处，供三处复用：

1. 应用层：``PosSession._init_browser`` launch 失败后的自愈重试；
2. 更新作业：``pip sync`` 之后同步浏览器（``PipDepsSyncAdapter`` 之后的步骤）——
   参见 ``services.release_update.job_adapters.PlaywrightBrowserSyncAdapter``：
   补装失败或补装后仍缺 build 都会写**结构化降级告警**（``[update][DEGRADED]`` 行，
   经 ``GET /api/release-update/job`` 的 ``log_tail`` 进 Admin「系统更新」面板），
   不再让缺失只停留在进程日志里、拖到采集器启动才暴露；
3. Docker entrypoint：容器启动前的健康检查（shell 侧调用同一命令）。

校验口径与分类
--------------
:func:`browser_cache_status` 对照「lib 期望的 build」与「缓存目录里实际存在的
build」，结论分为 ``ok`` / ``browser_missing`` / ``lib_unavailable`` /
``cache_unreadable`` 四类（见 :data:`REASON_OK` 等）。目录名规则与 playwright
driver 逐字一致：``browserDirectoryPrefix.replace("-", "_") + "-" + revision``
（``chromium`` → ``chromium-<rev>``、``chromium-headless-shell`` →
``chromium_headless_shell-<rev>``），缓存根由 ``PLAYWRIGHT_BROWSERS_PATH`` 决定、
未设时用平台默认目录。注意这是**目录名级别的代理判据**：目录存在但内容被截断只能
靠 ``playwright install`` 修，所以流程永远是「先 install（幂等）再校验」。

``playwright install chromium`` 自 1.49 起会同时安装 ``chromium`` 与
``chromium_headless_shell``，headless 启动用的正是后者，因此只需这一条命令。
反复执行是幂等的：目标 build 已存在时秒退。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from config import settings

logger = logging.getLogger(__name__)

# Playwright 在浏览器缺失时给出的固定措辞（异步/同步 API 一致）。
_BROWSER_MISSING_MARKERS = (
    "executable doesn't exist",
    "executable does not exist",
    "looks like playwright was just installed or updated",
)

# 补装浏览器用的进程内串行锁：**每个事件循环一把**，弱引用持有。
#
# 不要在模块级 `asyncio.Lock()`：它在**发生竞争**时绑定首个使用它的 loop，之后在别的
# loop 上一竞争就抛 "is bound to a different event loop"（`services/db_migrations.py`
# 记录的正是这个形状）。也**不能只做惰性创建**：锁一旦创建就常驻，第二个 loop 上照样
# 会撞同一句话（实测：两个 `asyncio.run(main())` + 4 路并发，第二次必炸）。
# 按 loop 分桶才真的等价于"同进程内同一时刻只有一次补装"，同时不携带跨 loop 状态。
_install_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _get_install_lock() -> asyncio.Lock:
    """取当前事件循环的补装锁（首次调用时创建）。"""
    loop = asyncio.get_running_loop()
    lock = _install_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _install_locks[loop] = lock
    return lock


def is_browser_missing_error(exc: "BaseException | str") -> bool:
    """异常是否表示「浏览器二进制缺失」（而非磁盘满、权限、网络等其它原因）。"""
    message = str(exc).lower()
    return any(marker in message for marker in _BROWSER_MISSING_MARKERS)


def browsers_path() -> Optional[str]:
    """当前生效的 PLAYWRIGHT_BROWSERS_PATH（None 表示用 Playwright 默认目录）。"""
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
    async with _get_install_lock():
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


# --- 版本一致性守卫：lib 期望的 build ↔ 缓存里实际有的 build --------------------

# 缓存根环境变量：安装与校验必须读同一个值，否则会各看各的目录。
BROWSER_CACHE_ENV = "PLAYWRIGHT_BROWSERS_PATH"

# 启动期真正会被 launch 的两个浏览器（`playwright install chromium` 同时装这两个）：
# chromium_headless_shell 是 headless 启动的默认目标，chromium 供 headed/子进程路径。
REQUIRED_BROWSER_NAMES: tuple[str, ...] = ("chromium", "chromium-headless-shell")

# 结构化降级告警码。写进更新作业日志的那一行由
# ``GET /api/release-update/job`` 的 ``log_tail`` 送进 Admin「系统更新」面板，
# 字段名是稳定契约，前端/运维脚本可以按它判断。
DEGRADED_PLAYWRIGHT_BROWSER_MISSING = "playwright_browser_missing"

# 校验结论分类：给出机器可判的原因，而不是只留一句人话。
REASON_OK = "ok"
REASON_BROWSER_MISSING = "browser_missing"
REASON_LIB_UNAVAILABLE = "lib_unavailable"
REASON_CACHE_UNREADABLE = "cache_unreadable"

# 硬失败开关：默认「结构化告警 + 不阻断更新」（与「不让一次网络抖动卡死整台店」的
# 既有决策对齐，见 PlaywrightBrowserSyncAdapter 的 docstring）；置 1/true/yes/on 时
# 「补装自称成功却仍缺 build」会抛异常让更新作业标红 + 回滚。
STRICT_ENV = "LUYUN_PLAYWRIGHT_STRICT_BROWSERS"

# 更新作业日志里那一行的前缀（单行 JSON 跟在后面，便于 grep 与解析）。
DEGRADED_ALERT_PREFIX = "[update][DEGRADED]"


class PlaywrightBrowserDriftError(RuntimeError):
    """lib 期望的浏览器 build 不在缓存里（确定性漂移，不是网络抖动）。"""

    def __init__(self, status: "BrowserCacheStatus") -> None:
        self.status = status
        super().__init__(status.describe())


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def strict_mode_enabled() -> bool:
    """是否要求「缺 build 即失败」（默认关，见 :data:`STRICT_ENV`）。"""
    return _truthy(os.environ.get(STRICT_ENV))


@dataclass(frozen=True)
class BrowserCacheStatus:
    """一次「lib 期望 ↔ 缓存实有」校验的结论（可分类、可序列化）。"""

    ok: bool
    reason: str
    cache_dir: Optional[str] = None
    expected: Mapping[str, str] = field(default_factory=dict)
    present: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    detail: str = ""

    @property
    def expected_dirs(self) -> tuple[str, ...]:
        return tuple(
            browser_directory_name(name, revision)
            for name, revision in self.expected.items()
        )

    def describe(self) -> str:
        """一句人话（日志/CLI 默认输出用）。"""
        if self.ok:
            return (
                "Playwright 浏览器与 lib 一致："
                f"{', '.join(self.expected_dirs)}（{self.cache_dir}）"
            )
        expected = ", ".join(self.expected_dirs) or "（读不到 lib 期望表）"
        present = ", ".join(self.present) or "（空）"
        missing = ", ".join(self.missing) or "（无）"
        text = (
            f"Playwright 浏览器不一致（{self.reason}）：期望 {expected}，"
            f"缓存 {self.cache_dir} 里只有 {present}，缺 {missing}"
        )
        return f"{text}；{self.detail}" if self.detail else text

    def as_alert(self, **extra: Any) -> dict[str, Any]:
        """结构化告警载荷（``degraded`` 是稳定字段名）。

        健康（``ok=True``）时 ``degraded`` 为 ``None``：键始终存在，消费方的字段集
        稳定（不必先判 key 再取值），但**不**在健康机上造出假降级标记——``--check
        --json`` 是给运维/自动化读的，``ok: true`` 旁边挂着 ``degraded:
        playwright_browser_missing`` 会误导消费方（评审 R-T6-01）。

        更新作业日志那一行不受影响：``format_degraded_alert()`` 只在 ``ok=False``
        的分支被调用（``job_adapters`` 的告警路径），``degraded`` 仍是常量，逐字不变。
        """
        payload: dict[str, Any] = {
            "degraded": None if self.ok else DEGRADED_PLAYWRIGHT_BROWSER_MISSING,
            "ok": self.ok,
            "reason": self.reason,
            "expected": dict(self.expected),
            "expected_dirs": list(self.expected_dirs),
            "missing": list(self.missing),
            "present": list(self.present),
            "cache_dir": self.cache_dir,
            "detail": self.detail,
        }
        payload.update(extra)
        return payload


def playwright_package_dir() -> Optional[Path]:
    """已安装 playwright 包目录（找它自带的 browsers.json）；找不到返回 None。"""
    try:
        spec = importlib.util.find_spec("playwright")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).resolve().parent


def browsers_json_path(package_dir: Optional[Path] = None) -> Optional[Path]:
    """``playwright/driver/package/browsers.json`` 的路径（不存在返回 None）。"""
    base = Path(package_dir) if package_dir is not None else playwright_package_dir()
    if base is None:
        return None
    path = base / "driver" / "package" / "browsers.json"
    return path if path.is_file() else None


def expected_browser_builds(
    names: Sequence[str] = REQUIRED_BROWSER_NAMES,
    *,
    package_dir: Optional[Path] = None,
) -> dict[str, str]:
    """lib 期望的浏览器 build——**唯一事实来源**是 lib 自带的 ``browsers.json``。

    返回 ``{browserName: revision}``，名字用 playwright 自己的写法
    （``chromium-headless-shell``）；调用方写 ``chromium_headless_shell`` 也接受。
    读不到、解析失败或缺少目标条目时抛 ``FileNotFoundError`` / ``ValueError``，
    由 :func:`browser_cache_status` 归类为 ``lib_unavailable``。
    """
    path = browsers_json_path(package_dir)
    if path is None:
        raise FileNotFoundError(
            "找不到 playwright 自带的 browsers.json（playwright 未安装或包结构变了）"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    wanted = {name.replace("_", "-") for name in names}
    found: dict[str, str] = {}
    for entry in data.get("browsers") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if name in wanted:
            found[name] = str(entry.get("revision"))
    missing = sorted(wanted - set(found))
    if missing:
        raise ValueError(f"{path} 里没有 {missing} 的条目")
    return found


def browser_directory_name(name: str, revision: str | int) -> str:
    """playwright 在缓存里的目录名（与 driver 的命名规则逐字一致）。"""
    return f"{name.replace('-', '_')}-{revision}"


def browsers_cache_dir(
    *,
    package_dir: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
    home: Optional[str] = None,
) -> Path:
    """playwright 实际使用的缓存根（与 driver 的 registryDirectory 规则一致）。

    ``PLAYWRIGHT_BROWSERS_PATH`` 优先；特殊值 ``0`` 表示「装在包目录下的
    ``.local-browsers``」；未设时按平台默认并拼 ``ms-playwright``：darwin
    ``~/Library/Caches``、linux ``$XDG_CACHE_HOME|~/.cache``、win ``%LOCALAPPDATA%``。
    env/platform/home 可注入，是为了让用例离线固定结果（本函数不联网）。
    """
    env = os.environ if env is None else env
    override = (env.get(BROWSER_CACHE_ENV) or "").strip()
    if override == "0":
        base = Path(package_dir) if package_dir is not None else playwright_package_dir()
        return (base if base is not None else Path.cwd()) / ".local-browsers"
    if override:
        return Path(override)
    system = platform or sys.platform
    if system == "darwin":
        return Path(home or os.path.expanduser("~")) / "Library" / "Caches" / "ms-playwright"
    if system.startswith("win"):
        local = env.get("LOCALAPPDATA") or str(
            Path(home or os.path.expanduser("~")) / "AppData" / "Local"
        )
        return Path(local) / "ms-playwright"
    root = env.get("XDG_CACHE_HOME") or str(
        Path(home or os.path.expanduser("~")) / ".cache"
    )
    return Path(root) / "ms-playwright"


def cached_browser_dirs(cache_dir: Optional[Path] = None) -> Optional[tuple[str, ...]]:
    """缓存根下的子目录名（按名排序）；目录不可读/同名文件挡路返回 None。"""
    base = Path(cache_dir) if cache_dir is not None else browsers_cache_dir()
    try:
        if not base.exists():
            return ()
        if not base.is_dir():
            return None
        return tuple(sorted(entry.name for entry in base.iterdir() if entry.is_dir()))
    except OSError:
        return None


def browser_cache_status(
    *,
    names: Sequence[str] = REQUIRED_BROWSER_NAMES,
    package_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
) -> BrowserCacheStatus:
    """对照「lib 期望的 build」与「缓存实有」给出结论（不联网、不装东西）。"""
    try:
        expected = expected_browser_builds(names, package_dir=package_dir)
    except (OSError, ValueError) as exc:
        return BrowserCacheStatus(
            ok=False, reason=REASON_LIB_UNAVAILABLE, detail=str(exc)
        )
    base = (
        Path(cache_dir)
        if cache_dir is not None
        else browsers_cache_dir(package_dir=package_dir)
    )
    dirs = cached_browser_dirs(base)
    if dirs is None:
        return BrowserCacheStatus(
            ok=False,
            reason=REASON_CACHE_UNREADABLE,
            cache_dir=str(base),
            expected=expected,
            detail="缓存目录不可读（权限不足或同名文件挡路）",
        )
    wanted = {browser_directory_name(name, rev) for name, rev in expected.items()}
    missing = tuple(sorted(wanted - set(dirs)))
    present = tuple(name for name in dirs if name.startswith("chromium"))
    return BrowserCacheStatus(
        ok=not missing,
        reason=REASON_OK if not missing else REASON_BROWSER_MISSING,
        cache_dir=str(base),
        expected=expected,
        present=present,
        missing=missing,
    )


def repair_hint(python_bin: Optional[str] = None) -> str:
    """补装命令（告警与 CLI 里都给出同一条，避免各写各的）。"""
    return f"{python_bin or sys.executable} -m playwright install chromium"


def format_degraded_alert(status: BrowserCacheStatus, **extra: Any) -> str:
    """更新作业日志里的那一行结构化告警（单行 JSON，便于 grep/解析）。"""
    payload = status.as_alert(**extra)
    return f"{DEGRADED_ALERT_PREFIX} " + json.dumps(
        payload, ensure_ascii=False, sort_keys=True
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行守卫：``python -m services.playwright_env [--check] [--json]``。

    缺 build 时**非零退出**，可直接用在 bootstrap / CI / 手工排查：

        .venv/bin/python -m services.playwright_env --check
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Playwright lib ↔ 浏览器 build 一致性守卫（读 lib 自带 browsers.json）"
    )
    parser.add_argument(
        "--check", action="store_true", help="校验并返回退出码（默认动作）"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出校验结果")
    parsed = parser.parse_args(list(argv) if argv is not None else None)

    status = browser_cache_status()
    if parsed.json:
        payload = status.as_alert(component="playwright_browser")
        payload["browsers_path_env"] = browsers_path()
        package_dir = playwright_package_dir()
        payload["package_dir"] = str(package_dir) if package_dir else None
        payload["repair"] = repair_hint()
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(status.describe())
        if not status.ok:
            print(f"  修复：{repair_hint()}")
    return 0 if status.ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
