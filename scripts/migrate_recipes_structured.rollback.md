# `migrate_recipes_structured.py` 回滚说明

本脚本只改 `sop_recipes` 上的结构化 JSON 列、`legacy_markdown` 快照和 `needs_review`。
**不**清空、不删除 `body_markdown`，也**不**删除 `legacy_markdown`（审计轨迹）。

待复核期间阅读/打印/Word 走 `body_markdown`，清 JSON 会让已确认的配方也退回 Markdown 兜底。

先停应用再操作。

## 方式一：清空结构化字段（推荐）

把拆分结果撤掉，让阅读页重新走 `body_markdown` 兜底。可选是否把 `needs_review` 置回 0。
**不要** `UPDATE … SET legacy_markdown = NULL`。

```sql
-- 在目标库上执行（生产是 data/app.db；先确认路径）
UPDATE sop_recipes
SET ingredients_json = NULL,
    steps_json = NULL,
    tips_json = NULL,
    needs_review = 0
WHERE legacy_markdown IS NOT NULL
  AND TRIM(legacy_markdown) != '';
```

若只想清 JSON、仍保持「待复核」标记，去掉上面的 `needs_review = 0`。

## 方式二：用迁移前备份覆盖（最后手段）

正式迁移（非 `--dry-run`）开始前，若目标 db 已存在，会复制：

```
<data/app.db>.bak.<YYYYmmdd_HHMMSS>
<data/app.db>.bak.<YYYYmmdd_HHMMSS>-wal   # 若存在
<data/app.db>.bak.<YYYYmmdd_HHMMSS>-shm   # 若存在
```

这会整库回到备份时刻，不只撤销配方拆分。仅在方式一不够用时使用：

```bash
# 停应用后，<timestamp> 换成实际备份名
cp data/app.db.bak.<timestamp> data/app.db
cp data/app.db.bak.<timestamp>-wal data/app.db-wal 2>/dev/null || true
cp data/app.db.bak.<timestamp>-shm data/app.db-shm 2>/dev/null || true
```

## 关于 dry-run

`--dry-run` 只读、只打印报告，不创建备份、不写库。没有「回滚 dry-run」。

## 关于幂等重跑

默认只处理用料/步骤/小贴士全空、且正文非空的行。已有快照时不覆盖 `legacy_markdown`。
回滚清 JSON 后，默认重跑会再拆这些行（快照保留）。`--force` 对已有结构化字段也重拆 JSON，仍不覆盖已有快照。
