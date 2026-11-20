-- 0061：排班「每日需求人数」——营业日 × 班次的目标人数
--
-- 背景：月历与班次表一直只把人数摊开，服务层没有「这天这个班最少要几个人」这个配置
-- （store.py 的 inbox 注释原话）。店长要回答「人够不够」，就得有一个目标值可配。
--
-- 两张表分工：
--   * scheduling_demand_defaults —— 平时 / 周末两套默认值（kind = weekday / weekend），
--     按班次各配一个数。这是「常规口径」，不随日期变。
--   * scheduling_demand_days —— 某一天某个班次的**按天覆盖**（节假日、活动日）。
--     只存被覆盖的格子；没存的格子回落默认值，再没有就回落历史中位数（服务层算）。
--
-- 没有配置时服务层不在这里落行，而是按「近 4 周同星期几的中位数」现算并标
-- source='fallback' —— 所以这两张表只存人配过的东西，空表就是「还没配」。
--
-- 幂等：IF NOT EXISTS，可重复执行；纯加成，不 DROP 任何东西。

-- 默认值。tenant_id 与既有排班表同口径（0005_scheduling.sql）：单店阶段恒为 1，
-- 多店时靠它隔离；写入口只在 store 那一层，SQL 里不另开旁路。
CREATE TABLE IF NOT EXISTS scheduling_demand_defaults (
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "kind" TEXT NOT NULL,
    "shift_id" BIGINT NOT NULL,
    "headcount" BIGINT NOT NULL,
    PRIMARY KEY ("tenant_id", "kind", "shift_id"),
    CONSTRAINT scheduling_demand_defaults_kind_check
        CHECK ("kind" IN ('weekday', 'weekend')),
    CONSTRAINT scheduling_demand_defaults_headcount_check
        CHECK ("headcount" >= 0)
);

-- 按天覆盖。business_date 沿用排班既有口径：TEXT 的 ISO 日期（见 staff_assignments），
-- 不做 DATE 类型 —— 营业日由 services/business_day.py 算，SQL 不参与切日。
CREATE TABLE IF NOT EXISTS scheduling_demand_days (
    "tenant_id" BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "business_date" TEXT NOT NULL,
    "shift_id" BIGINT NOT NULL,
    "headcount" BIGINT NOT NULL,
    PRIMARY KEY ("tenant_id", "business_date", "shift_id"),
    CONSTRAINT scheduling_demand_days_headcount_check
        CHECK ("headcount" >= 0)
);

-- 扫一段日期（月历 / 周表 / 近 4 周参考值）时按 tenant_id + business_date 走这条索引；
-- 单店阶段 tenant_id 恒为 1，前导列照既有排班表的口径带上，多店时不用再补迁移。
CREATE INDEX IF NOT EXISTS idx_scheduling_demand_days_date
    ON scheduling_demand_days ("tenant_id", "business_date");
