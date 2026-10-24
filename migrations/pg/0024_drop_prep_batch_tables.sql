-- 0024_drop_prep_batch_tables.sql
--
-- 备货计划不再登记备货数量：每次计算都当 0 库存（ADR 0111）。
-- 「登记 → 批次 → 可用量 → 按过期时段分桶抵扣」这一整套退场，连同它的两张表，
-- 以及三张计划快照表上只服务那一套的废列。
--
-- ⚠️ **这不是加成性变更。** `README.md` 的规矩是增量脚本只做加成性变更
-- （加列带默认值 / 加索引 / 建新表），好让旧代码在新 schema 上仍能跑。这里**故意破例**：
--
--   * 部署形态是单机单实例，更新作业在 `applying_migrations` 阶段同批切代码与 schema，
--     不存在「旧代码跑新 schema」的窗口（ADR 0096、deploy/README.md）；
--   * 被删的是**执行记录**，备份覆盖率本来就不高，而且生产库里只有 2026-04-27 那天的
--     18 行批次 + 18 行流水，此后无人登记 —— 这正是它退场的原因。
--
-- **数据不可恢复。** 应用之后那 36 行就没了。要留档就先把库备份一次
-- （管理后台「备份中心 → 导出备份」打的是整库 pg_dump），或单独导出：
--     pg_dump -t prep_batches -t prep_stock_movements luyun > prep_batches_backup.sql
--
-- 幂等：全部带 IF EXISTS，可重复执行。新代码既不读也不写这些表与列，
-- 所以「先跑迁移」还是「先切代码」都不影响可用性 —— 让迁移能独立于发布顺序应用。

DROP TABLE IF EXISTS prep_stock_movements;
DROP TABLE IF EXISTS prep_batches;

-- 三张计划快照表在 `0030`（ADR 0117 重建）里会被整张删掉。这里的 DROP COLUMN 因此要
-- 允许「表已经不在」：迁移记录被清空后**按顺序重放**（发行包升级适配器的用例就是这么跑的）
-- 时，`0030` 已经动过手，`ALTER TABLE prep_plan_items ...` 会在表不存在处直接报错，
-- 整次重放就断在这儿。用 `to_regclass` 判一下，已应用过的门店与全新建库都走同一条路。
DO $$
BEGIN
    IF to_regclass('prep_plan_items') IS NOT NULL THEN
        ALTER TABLE prep_plan_items DROP COLUMN IF EXISTS available_qty;
        ALTER TABLE prep_plan_items DROP COLUMN IF EXISTS risk_level;
    END IF;
    IF to_regclass('prep_plan_item_slots') IS NOT NULL THEN
        ALTER TABLE prep_plan_item_slots DROP COLUMN IF EXISTS available_qty;
    END IF;
    IF to_regclass('prep_plan_runs') IS NOT NULL THEN
        ALTER TABLE prep_plan_runs DROP COLUMN IF EXISTS high_risk_count;
        ALTER TABLE prep_plan_runs DROP COLUMN IF EXISTS expiry_risk_count;
        ALTER TABLE prep_plan_runs DROP COLUMN IF EXISTS waste_risk_count;
    END IF;
END $$;
