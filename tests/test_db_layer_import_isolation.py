#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分层不变量：`db_core` 不得依赖 `services`（DOC-07 / ticket 18，T3-V1）。

为什么必须是子进程：`tests/conftest.py` 在第一处测试之前就已经 import 过 `services`
（config / database 整图），同进程里 `sys.modules` 早被污染，断言没有任何鉴别力。
所以这里起一个干净的 `sys.executable -c`，只导入 db_core 的三个被反向依赖过的模块，
再回看 `sys.modules`。

不变量（原先被打破的两个点）：
- `import db_core.aggregation` 直接 ImportError：
  db_core.aggregation → services.urgency_policy → services/__init__ → prep_plan_service
  → database → db_core.aggregation（半初始化模块）。
- `db_core/orders_repo.py` 的 `from services.urgency_policy import level_for_wait_ms` 同理
  （services 包的 __init__ 有副作用，不是"单向就没问题"）。

策略函数的唯一实现因此下沉到 `db_core/urgency_policy.py`，`services/urgency_policy.py`
只剩 re-export。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE_MARKER = "SERVICES_MODULES="
_PROBE = (
    "import json, sys;"
    "import db_core.aggregation, db_core.orders_repo, db_core.reports;"
    "print('"
    + _PROBE_MARKER
    + "' + json.dumps(sorted(m for m in sys.modules if m == 'services' or m.startswith('services.'))))"
)


def _run_probe() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_db_layer_imports_without_pulling_services():
    proc = _run_probe()
    assert proc.returncode == 0, f"db_core 三个模块无法独立导入:\n{proc.stdout}\n{proc.stderr}"

    marked = [line for line in proc.stdout.splitlines() if line.startswith(_PROBE_MARKER)]
    assert marked, f"探针没有输出 {_PROBE_MARKER} 行:\n{proc.stdout}\n{proc.stderr}"
    pulled = json.loads(marked[-1][len(_PROBE_MARKER):])
    assert pulled == [], f"导入 db_core 时被连带拉起了 services 模块: {pulled}"


def test_no_module_level_services_import_in_db_core():
    """静态面：db_core/**.py 里不允许出现模块级（顶格）的 `from services …` / `import services…`。

    用 AST 而不是 grep，是为了不被注释/文档字符串里的示例误导；`col_offset == 0`
    等价于票面 grep 的 `^from services`（函数内的惰性导入不算，它们只在调用时执行）。
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "db_core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.col_offset != 0:
                    continue
                target = node.module or ""
                if target == "services" or target.startswith("services."):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} from {target}")
            elif isinstance(node, ast.Import):
                if node.col_offset != 0:
                    continue
                for alias in node.names:
                    if alias.name == "services" or alias.name.startswith("services."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} import {alias.name}")

    assert offenders == [], f"db_core 仍有模块级 services 导入: {offenders}"


def test_services_urgency_policy_is_a_pure_reexport():
    """旧路径只能是壳：两个模块里的名字必须是**同一个函数对象**（没有第二份实现）。"""
    import db_core.urgency_policy as canonical
    import services.urgency_policy as shim

    for name in (
        "urgent_threshold_ms",
        "high_threshold_ms",
        "level_for_wait_ms",
        "urgent_cutoff",
        "high_cutoff",
    ):
        assert getattr(shim, name) is getattr(canonical, name), f"services.urgency_policy.{name} 不是 db_core 的实现"
