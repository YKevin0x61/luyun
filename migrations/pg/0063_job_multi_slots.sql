-- 0063：推送任务多触发点
--
-- 一条定时推送任务从「一个时刻」变成「一组时刻」（.scratch/push-slots/ 的访谈结论，
-- 2026-11-23）。一天里想推两次（11:30 的中午看板 + 21:30 的日终）不用再建两条任务，
-- 同时也就没有了「两条任务撞同一分钟互相吞」的幂等问题。
--
--   * slots_json     JSON 数组，每项 {"weekday": 0-6 或 null, "time": "HH:MM"}。
--                    weekday 为 null = 每天都发；空数组 = 旧行还没写过，读取侧回退
--                    到 schedule_time（见 services/wecom_push_slots.py 的 slots_from_row）。
--   * last_sent_json {营业日: {"HH:MM": 发送时刻 ISO}}，按触发点记「这个点今天发过没」。
--                    只留最近 7 个营业日（写入时裁剪），不是流水账。
--
-- schedule_time / last_sent_date 两列继续写（主触发点 / 任一触发点已发），是留给还在
-- 缓存旧 bundle 的浏览器的兼容投影，不是新的数据源。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + 只回填 slots_json 还空着的行，可重复执行；
-- 纯加成，不 DROP，不改既有列。

ALTER TABLE "wecom_push_jobs"
    ADD COLUMN IF NOT EXISTS "slots_json" TEXT NOT NULL DEFAULT '[]';

ALTER TABLE "wecom_push_jobs"
    ADD COLUMN IF NOT EXISTS "last_sent_json" TEXT NOT NULL DEFAULT '{}';

-- 每周类（下周排班表）：params 里的 weekday 就是它的「哪一天」，跟着搬进 slots。
UPDATE "wecom_push_jobs"
SET "slots_json" = json_build_array(json_build_object(
        -- 取文本再转 int：params_json 里的 weekday 存的是 JSON 数字时能转，
        -- 存成字符串 "6"（旧页面 select 交上来的就是字符串）也能转。
        'weekday', (NULLIF(btrim("params_json"), '')::jsonb ->> 'weekday')::int,
        'time', "schedule_time"
    ))::text
WHERE COALESCE("slots_json", '[]') IN ('', '[]')
  AND "topic_id" = 'weekly_schedule'
  AND COALESCE("schedule_time", '') <> ''
  -- 用 jsonb_exists 而不是 ?? 运算符：迁移执行器会把 SQL 里的 ? 当成占位符改写成 $1。
  AND jsonb_exists(NULLIF(btrim("params_json"), '')::jsonb, 'weekday');

-- 其余内容类型：每天都发，只有时刻。
UPDATE "wecom_push_jobs"
SET "slots_json" = json_build_array(json_build_object(
        'weekday', NULL,
        'time', "schedule_time"
    ))::text
WHERE COALESCE("slots_json", '[]') IN ('', '[]')
  AND COALESCE("schedule_time", '') <> '';

-- 旧行只记了「最近一次发过的营业日」，没有按触发点分。补成
-- {该营业日: {该时刻: ""}}（空串 = 发过但没记下具体时刻）：不补的话，上线当天
-- 已经用旧逻辑发过的那一条会被读成「这个点今天还没发」，再推一遍。
UPDATE "wecom_push_jobs"
SET "last_sent_json" = json_build_object(
        "last_sent_date",
        json_build_object("schedule_time", '')
    )::text
WHERE COALESCE("last_sent_json", '{}') IN ('', '{}')
  AND COALESCE("last_sent_date", '') <> ''
  AND COALESCE("schedule_time", '') <> '';
