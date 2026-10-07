-- 0018 — 推送任务不再持有收件人：内容类型 + 参数进列，旧列只读
--
-- 背景（ADR 0094，票 08）：推送任务从「内容类型 + 它绑定的一个渠道」变成
-- 「内容类型 + 参数 + 时间」——**收件人由推送订阅决定**。任务不再持有收件人之后，
-- 每条旧任务的收件人靠迁移 0016 的第三条回填规则保住（「每条现有任务的
-- 内容类型 × 它绑定的渠道」已经写进那个内容类型的订阅），所以本脚本只负责让任务
-- 认识自己的**内容类型**与**参数**，一个字都不改订阅。
--
-- 三件事，都是加成性变更：
--   1. 加 `topic_id`（内容类型）：按旧 `push_type` 回填，对照表与 0016 逐字一致 ——
--      sales_report_text  → sales_report
--      data_quality_alert → reconcile_diff
--   2. 加 `params_json`（参数）：把旧列里的推送时间 / 日期口径 / 档口搬进去。参数是
--      入队时唯一的校验与渲染来源，漏搬就会让旧任务改用注册表的**默认值**（时间与
--      日期口径都会漂，日报可能变成另一天或另一个时间）。
--   3. 给 `webhook_id` 补默认值：新代码不再写这一列，而它是 NOT NULL 且没有默认值 ——
--      不补的话 INSERT 直接失败。旧列与旧值**原样保留、只读**（回滚与排查的依据）。
--
-- 可重复执行：`ADD COLUMN IF NOT EXISTS` + 只填空值的回填（`params_json = '{}'`、
-- `topic_id` 为空）。管理员在「数据库迁移」面板上重跑一次不会覆盖已经填好的值。
--
-- 内容类型 id 与注册表（`services/wecom_push_topics.py`）必须一致：任务的 topic_id
-- 决定它入队时用哪个参数 schema，也对上它在 0016 里回填出来的那批订阅。

ALTER TABLE wecom_push_jobs ADD COLUMN IF NOT EXISTS "topic_id" TEXT;
ALTER TABLE wecom_push_jobs ADD COLUMN IF NOT EXISTS "params_json" TEXT NOT NULL DEFAULT '{}';

-- 旧列 `webhook_id` 不再被读，但必须能插（NOT NULL 且无默认值时 INSERT 会失败）。
ALTER TABLE wecom_push_jobs ALTER COLUMN "webhook_id" SET DEFAULT 0;

-- 回填内容类型。`push_type` 只有两个取值（见 0001 的建表默认值与 0016 的对照表）；
-- 认不出来的旧值一律按销售报表处理，不丢这一行。
UPDATE wecom_push_jobs
   SET "topic_id" = CASE "push_type"
                        WHEN 'data_quality_alert' THEN 'reconcile_diff'
                        ELSE 'sales_report'
                    END
 WHERE "topic_id" IS NULL OR "topic_id" = '';

-- 回填参数。字段集按内容类型分开：对账差异告警的定时侧**没有**「档口」这个参数
-- （见 `ReconcileDiffScheduleParams`），多塞一个字段会被注册表的 schema 拒绝
-- （定时侧 `extra="forbid"`），那条任务从此一次都发不出去。
UPDATE wecom_push_jobs
   SET "params_json" = json_build_object(
           'schedule_time', "schedule_time",
           'date_range_mode', "date_range_mode",
           'station', COALESCE("station", '')
       )::text
 WHERE "topic_id" = 'sales_report' AND "params_json" = '{}';

UPDATE wecom_push_jobs
   SET "params_json" = json_build_object(
           'schedule_time', "schedule_time",
           'date_range_mode', "date_range_mode"
       )::text
 WHERE "topic_id" = 'reconcile_diff' AND "params_json" = '{}';

-- 回填之后内容类型由代码保证非空（写入方总是显式给这一列）：这里只补默认值，
-- **不加 NOT NULL** —— 列保持可空，回填语句才能被重复执行验证（也留着「迁移前形状」
-- 的行可以被种出来），代价是读路径要把空值当「这条任务还没接上内容类型」处理。
ALTER TABLE wecom_push_jobs ALTER COLUMN "topic_id" SET DEFAULT 'sales_report';
