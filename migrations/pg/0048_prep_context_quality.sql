-- 0048_prep_context_quality.sql
--
-- 备货复盘环境信号补四项（票 08 / `.scratch/prep-review/spec.md` §12 P0/P1/P2）。
--
-- `prep_plan_context` 原先只存「当天整天不变」的外部原因（节假日 / 天气 / 在岗人数 /
-- 备注，见 0046）。第二轮补采要回答「那天的数据可不可信、当时按什么营业时段算、
-- 是不是工作日、厨房出得多快」，这四项与前四个字段不同：**其中三项会随一天推进
-- 而变**（订单数、快照覆盖小时、出餐行数），所以它们是「每次刷新都覆盖」的动态信号，
-- 不是一次落定的事实快照。存进同一张按天一行的小表即可，读取端一次拿全一天的解释项。
--
-- 四列各自的口径：
--
--   · `data_quality_json` —— {orders_today, first_order_at, last_order_at,
--     snapshot_hours, scraper_last_success}。`orders_today` 是当日订单行数（采集面，
--     不是销量面），`snapshot_hours` 是当日不同 `snapshot_at` 的个数；二者一起回答
--     「这天复盘用的样本够不够」。
--   · `runtime_json` —— {open, close, source}。来源优先级 `prep_plan_runtime` →
--     `scraper_runtime` → 默认 07:00 / 21:30；`source` 记录命中的配置键，便于解释
--     「为什么那天按 07:00 算」。
--   · `is_workday` —— 自然日 + `statutory_holidays`：节假日为非工作日。**周末按工作日**
--     （店里周末照常营业），**调休 / 补班暂不可知**（日历表只有节假日、没有补班）。
--     可空：只有备注、还没有任何信号的行不知道就是不知道，不编一个 true。
--   · `kitchen_json` —— {available, reason?, ready_lines, median_ready_minutes,
--     rushed_lines, hold_lines}。从当日 `orders` 聚合：`ready_time` 非空才算已出餐，
--     中位时长按 `ready_time - order_time` 取分钟；`is_rushed` / `is_hold` 计行数。
--     **门店 KDS 尚未上线**（2026-11-07 用户确认）：这四项的信号源只有 KDS 的出餐动作，
--     库里那点 `ready_time` 是试用期批量补的，所以采集在 `services/prep_plan_context.py`
--     的 `KDS_LIVE` 开关后面，现在恒写 `{"available": false, "reason": "kds_not_live"}`
--     并跳过整天订单扫描。KDS 上线后把开关改成 True 即恢复真实聚合，无需改表。
--
-- 幂等：全部 `ADD COLUMN IF NOT EXISTS`，可重复执行。纯加成性变更：不动既有列、
-- 不删行；加列带默认值，应用前后既有 context 行读出来仍是原来的四项。schema 不随
-- 启动生效，需在 Admin「系统更新 → 数据库迁移」或更新作业的 `applying_migrations`
-- 阶段应用（见 `migrations/pg/README.md`）。

ALTER TABLE "prep_plan_context"
    ADD COLUMN IF NOT EXISTS "data_quality_json" TEXT NOT NULL DEFAULT '{}';
ALTER TABLE "prep_plan_context"
    ADD COLUMN IF NOT EXISTS "runtime_json" TEXT NOT NULL DEFAULT '{}';
ALTER TABLE "prep_plan_context"
    ADD COLUMN IF NOT EXISTS "kitchen_json" TEXT NOT NULL DEFAULT '{}';
ALTER TABLE "prep_plan_context"
    ADD COLUMN IF NOT EXISTS "is_workday" BOOLEAN;
