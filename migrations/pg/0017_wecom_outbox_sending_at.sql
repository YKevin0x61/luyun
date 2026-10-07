-- 0017 — 出站记录补「进入发送的时刻」：卡在 sending 的行要能被兜底捞回来。
--
-- 缺陷（票 03 返工）：`wecom_outbox_mark_sending` 之后、写终态之前进程退出（崩溃 /
-- systemd 重启 / 更新作业重启应用），这一行会永远停在 `sending` —— 派发只捞
-- `pending`，于是它既不会被重发、也不会被标失败，页面上只剩一条卡住的行。
--
-- 兜底判定需要「这一行是什么时候进入 sending 的」，表里原先没有这个时间：
-- `created_at` 是**入队**时刻（可能早得多），`scheduled_at` 是下一次尝试的时刻，
-- 两者都推不出「正在发多久了」。所以加一列，由派发路径在 mark_sending 时写入。
--
-- 只做加成性变更（ADD COLUMN，默认 NULL，不建索引）：sending 的行在表里始终只有
-- 极少数，兜底查询靠既有的 `idx_wecom_push_outbox_pending (status, id)` 先按状态
-- 收敛，再逐行比时间就够了。
--
-- 存量兜底：迁移之前就已经卡在 sending 的行 sending_at 为 NULL，查询侧按
-- `COALESCE(sending_at, created_at)` 判定 —— created_at 必然不晚于进入 sending 的
-- 时刻，所以这些行不会因为缺值而永远捞不回来（只是可能早一点被捞）。

ALTER TABLE wecom_push_outbox ADD COLUMN IF NOT EXISTS "sending_at" TEXT;
