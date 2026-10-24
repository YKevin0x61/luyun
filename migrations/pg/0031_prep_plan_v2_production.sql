-- 0031_prep_plan_v2_production.sql
--
-- 备货计划 v2（ADR 0118）：开始记「实际做了多少」。
--
-- v1（ADR 0117）只勾不记数，所以「在库量」无从算起，营业中也就无法提醒「该再做一批」。
-- v2 在当日明细上加三列：这次做了多少（可累加）、谁勾的、什么时候勾的。
--
-- 这三列与 `suggested_qty` / `final_qty`（建议量、店长手改后的量）**互不覆盖**：
-- 前者是「顾客要多少」的推算，后者是「师傅实际做了多少」的事实，v2 刻意不把两者揉成
-- 一个数（ADR 0118 决策 5）。
--
-- 幂等：全部 IF NOT EXISTS，可重复执行。这是**加成性**变更，符合 README 的规矩。

ALTER TABLE "prep_plan_entries" ADD COLUMN IF NOT EXISTS "produced_qty" DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE "prep_plan_entries" ADD COLUMN IF NOT EXISTS "produced_by" TEXT NOT NULL DEFAULT '';
ALTER TABLE "prep_plan_entries" ADD COLUMN IF NOT EXISTS "produced_at" TEXT;
