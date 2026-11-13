-- 0055_staff_meal_planned_push.sql
--
-- 员工餐采购：推送时机改造（`.scratch/wb-review/push-schedule-contract.md`）。
--
-- 使用日 ≥ 后天时不再提交即推，而是定时到「使用日前一天 10:00（北京时）」发；
-- `planned_push_at` 记这一版的计划时刻（CHINA_TZ ISO 字符串，NULL = 非定时）。
-- 纯加列、可重复执行：不改任何既有列与数据，旧单据保持 NULL（= 立即推口径）。
ALTER TABLE "staff_meal_orders"
    ADD COLUMN IF NOT EXISTS "planned_push_at" TEXT;
