-- 0035_semi_rules_prep_item_id.sql
--
-- 配方系数接备货品主数据（.scratch/prep-rules/spec.md）。
--
-- 为什么加这一列：`semi_finished_rules.semi_name` 是自由文本，`prep_items.item_name`
-- 才是主数据，两边靠 `(名字, 单位)` 字符串对齐。实测 71 个半成品名里有 15 个对不上
-- 59 个备货品（粥类整类缺），这些对不上的规则**完全不产生备货建议**——菜照卖、料没人备。
-- 加一列外键之后，读取侧优先认 id，名字只做展示与兜底。
--
-- 为什么可空：存量 143 条由 `scripts/prep_items_backfill.py` 回填，回填前必须允许空；
-- 读取侧对空值回落到今天的名字匹配，所以上线顺序是「先加列 → 再跑回填」，中间不断流。
--
-- 为什么 ON DELETE SET NULL：删一个备货品不该连带删规则（那是两个动作），
-- 规则回到「没对齐」状态，页面与核对清单会标出来。
--
-- 幂等：IF NOT EXISTS，可重复执行。纯加成性变更，不动既有列、不改既有数据。

ALTER TABLE semi_finished_rules
    ADD COLUMN IF NOT EXISTS "prep_item_id" BIGINT
    REFERENCES prep_items(id) ON DELETE SET NULL;

-- 页面按 prep_item 反查「这个备货品被哪些菜用」，走这个索引。
CREATE INDEX IF NOT EXISTS "idx_semi_rules_prep_item"
    ON semi_finished_rules ("prep_item_id");
