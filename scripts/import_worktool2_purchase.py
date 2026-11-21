#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""worktool2 云开发导出 JSON → luyun 厨房采购（命令行入口）。

逻辑已搬到 services/purchase/importer.py，网页迁移页与这里共用同一套映射和报告。
用法不变：

    .venv/bin/python scripts/import_worktool2_purchase.py --source scripts/migrate/data/worktool2
    .venv/bin/python scripts/import_worktool2_purchase.py --source scripts/migrate/data/worktool2 --apply
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.purchase.importer import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
