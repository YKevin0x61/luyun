-- 0058_prep_actuals_censored.sql
--
-- 需求侧删失标记：售罄样本的真实需求只知道「至少卖了多少」，不能当精确销量用。
-- 落库两列，供复盘 / 回测与后续删失分位使用；预测算法暂不读取（ADR 0146）。
--
-- is_censored：该品-天是否命中「真停卖」的售罄事件（meta_json.reliable = true）。
-- demand_lower：需求下界，即窗口内实际销量；未售罄时它也就是精确需求。
--
-- 只加成性变更；幂等。

ALTER TABLE "prep_plan_actuals"
    ADD COLUMN IF NOT EXISTS "is_censored" BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS "demand_lower" DOUBLE PRECISION;

COMMENT ON COLUMN "prep_plan_actuals"."is_censored" IS
    '该品-天是否有可靠售罄（真停卖）事件；有则 sold_qty 只是需求下界';
COMMENT ON COLUMN "prep_plan_actuals"."demand_lower" IS
    '需求下界（窗口内实际销量）；is_censored 时真实需求 >= 该值';
