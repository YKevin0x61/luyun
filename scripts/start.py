#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅订单数据采集系统后端启动脚本

启动前先做一道**解释器漂移**检查（只提示、不阻断，见 `warn_interpreter_drift`）：
开发机惯用的系统 `python3` 与测试/生产用的仓库 `.venv` 不是同一套解释器，
三方库版本也会差一个大版本（PERF-04，`.scratch/project-review-2026-09-22`）。
"""

import sys
import os
import platform
import uvicorn
import logging
from pathlib import Path
from typing import Any, Optional

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config import settings

# 解释器漂移检查的基准路径（沿用上面的 project_root）。
VENV_DIR = project_root / ".venv"
REQUIREMENTS_FILE = project_root / "requirements.txt"

# 「调用方没传这个值」的哨兵：与显式传入 None（= 未安装）区分开。
_UNSET = object()


def parse_pinned_playwright(requirements_path: Path) -> Optional[str]:
    """解析 `requirements.txt` 里 `playwright==<版本>` 的钉死值。

    读不到文件、没有这一行、或写成范围约束（`playwright>=1.40`）时返回 None，
    调用方据此**跳过**版本判定，而不是猜一个版本。刻意不引入 packaging：
    这里只需要从一行字符串里取值。
    """
    try:
        text = Path(requirements_path).read_text(encoding="utf-8")
    except OSError:
        return None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        name, separator, version = line.partition("==")
        if not separator or name.strip().lower() != "playwright":
            continue
        version = version.strip()
        if version:
            return version
    return None


def _distribution_version(distribution: str) -> Optional[str]:
    """已安装三方库的版本；未安装或元数据不可读时返回 None。"""
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:
        # 提示路径上的任何异常都降级为「版本未知」：查不到版本不该让启动脚本崩。
        return None


def is_repo_venv(executable: str, venv_dir: Path) -> bool:
    """`executable` 是否位于仓库 `venv_dir` 内。

    用 `abspath` 而不是 `resolve()`：venv 的 `bin/python` 本身就是指向真实解释器
    的 symlink（macOS 与 uv 建的 venv 都是），把链接解析掉就会误判成「不在 venv 内」。
    """
    try:
        Path(os.path.abspath(executable)).relative_to(Path(os.path.abspath(venv_dir)))
    except ValueError:
        return False
    return True


def format_interpreter_warning(
    *,
    executable: str,
    venv_dir: Path,
    python_version: str,
    pinned_playwright: Optional[str],
    installed_playwright: Optional[str],
    installed_redis: Optional[str] = None,
) -> Optional[str]:
    """生成解释器漂移告警文案；环境与 `.venv` 一致时返回 None（静默）。

    判定与取真实环境的值分开，是为了让这段逻辑可以脱离当前解释器单测
    （见 `tests/test_start_interpreter_guard.py`）。
    """
    inside_venv = is_repo_venv(executable, venv_dir)
    # 钉死值解析不出来（requirements 改了写法 / 文件缺失）就不做版本判定：
    # 宁可少告警，也不猜一个版本去冤枉正常环境。
    version_known = pinned_playwright is not None
    version_matches = not version_known or installed_playwright == pinned_playwright
    if inside_venv and version_matches:
        return None

    installed_text = installed_playwright or "未安装"
    if version_known:
        playwright_line = f"{installed_text}（requirements.txt 钉死 {pinned_playwright}）"
    else:
        playwright_line = f"{installed_text}（requirements.txt 未钉死，不做版本判定）"

    reasons = []
    if not inside_venv:
        reasons.append(f"当前解释器不在仓库 .venv 内（{executable}）")
    if not version_matches:
        reasons.append(
            "playwright 版本与 requirements.txt 钉死值不一致"
            f"（{installed_text} != {pinned_playwright}）"
        )

    lines = [
        "=" * 72,
        "⚠️  解释器漂移：当前实例与测试/生产不是同一套环境",
        "-" * 72,
        f"  当前解释器 : {executable} (Python {python_version})",
        f"  playwright : {playwright_line}",
        f"  redis      : {installed_redis or '未安装'}",
        f"  仓库 .venv : {venv_dir}",
        "",
        "  原因：",
    ]
    lines += [f"    - {reason}" for reason in reasons]
    lines += [
        "",
        "  开发机上观测到的三方库行为（Redis 重连、Playwright 行为）不能当生产证据；",
        "  playwright 的 lib 还与 chromium build 强绑定（见 AGENTS.md「Important Gotchas」）。",
        "",
        "  改用 .venv/bin/python scripts/start.py",
        "  （服务照常启动，本条只提示，不阻断。）",
        "=" * 72,
    ]
    return "\n".join(lines)


def interpreter_drift_warning(
    *,
    executable: Optional[str] = None,
    venv_dir: Optional[Path] = None,
    requirements_path: Optional[Path] = None,
    python_version: Optional[str] = None,
    installed_playwright: Any = _UNSET,
    installed_redis: Any = _UNSET,
) -> Optional[str]:
    """按当前解释器与 `requirements.txt` 生成告警文案；无需告警时返回 None。

    默认参数走真实环境（`sys.executable` / 仓库 `.venv` / `requirements.txt`）；
    显式传入 `installed_playwright`（None 表示未安装）可脱离真实环境单测。
    """
    if installed_playwright is _UNSET:
        installed_playwright = _distribution_version("playwright")
    if installed_redis is _UNSET:
        installed_redis = _distribution_version("redis")
    return format_interpreter_warning(
        executable=executable or sys.executable,
        venv_dir=Path(venv_dir or VENV_DIR),
        python_version=python_version or platform.python_version(),
        pinned_playwright=parse_pinned_playwright(
            Path(requirements_path or REQUIREMENTS_FILE)
        ),
        installed_playwright=installed_playwright,
        installed_redis=installed_redis,
    )


def warn_interpreter_drift(**overrides: Any) -> bool:
    """漂移时打印醒目告警（stderr），一致时完全静默；返回是否打印过。"""
    message = interpreter_drift_warning(**overrides)
    if message is None:
        return False
    print(message, file=sys.stderr)
    return True


if __name__ == "__main__":
    # 只提示不阻断：裸 `python3 scripts/start.py` 照旧能起服务（既有习惯），
    # 但不该再拿这台机器上的三方库观测当生产结论。
    warn_interpreter_drift()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("🚀 启动餐厅订单数据采集系统后端服务...")
    print(f"📋 版本: {settings.APP_VERSION}")
    print(f"📡 服务地址: http://{settings.HOST}:{settings.PORT}")
    print(f"📚 API文档: http://{settings.HOST}:{settings.PORT}/docs")
    print(f"🔧 调试模式: {'开启' if settings.DEBUG else '关闭'}")
    print("=" * 50)
    
    try:
        # 启动服务器
        uvicorn.run(
            "main:app",
            host=settings.HOST,
            port=settings.PORT,
            reload=settings.DEBUG,
            # 不指定 reload_dirs 的话 uvicorn 只监视 cwd——本脚本从 scripts/ 启动，
            # 于是改 services/、config.py 都不会触发热重载（现场踩过：改了代码
            # 以为已生效，其实进程还跑着旧逻辑）。这里显式盯仓库根。
            reload_dirs=[str(project_root)] if settings.DEBUG else None,
            workers=settings.WORKERS,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("🛑 用户中断，系统关闭")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1) 
