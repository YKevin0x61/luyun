-- 0032_prep_plan_drop_production_columns.sql
--
-- 备货计划 v3（ADR 0121）：不登记产量，页面只给「今天参考值」。
--
-- 店长原话：「不需要登记做了多少的数量，员工自己点开备货页面参考值的数量 − 现实中的
-- 库存数量 = 要备货的数量」。产量一旦不登记，在库量、补货提醒、复盘偏差三样都失去
-- 数据源，整层随 ADR 0118 一起退场 —— 留着死表比删掉更糟，会让人以为系统在替她管库存。
--
-- 删的是 0031 加的三列（produced_qty / produced_by / produced_at）。
-- **不删** done_at / done_by（勾选还在用）与 final_qty / qty_source（店长手改还在用）。
--
-- 注意：这是**破坏性**变更（DROP COLUMN），与 README「只做加成性变更」的默认规矩相反。
-- 这是用户明确裁定的一次性清理（2026-10-26），删前已确认全仓只有 prep_plan 自己的
-- 服务/接口/前端/测试读写这三列，且生产库里 produced_qty > 0 的行数为 0。
--
-- 幂等：IF EXISTS，可重复执行。

ALTER TABLE "prep_plan_entries" DROP COLUMN IF EXISTS "produced_qty";
ALTER TABLE "prep_plan_entries" DROP COLUMN IF EXISTS "produced_by";
ALTER TABLE "prep_plan_entries" DROP COLUMN IF EXISTS "produced_at";
