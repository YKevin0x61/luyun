-- 0014：分享记录支持「配对图」（专项的前后对照）
--
-- 专项卫生一次提交带两张图（前 / 后）。`capture_id` 存**后**（结果图），这一列存
-- **前**；两条一起现场拼成一张左前右后的对照图发出去，仍然只算**一次**分享
-- （一条 dedupe_key、一条消息）。
--
-- 单独出一张增量脚本、而不是回头改 0013：结构变更要可追溯，且 000N 一旦发布就不再改。
-- 默认空串 = 没有配对图，既有行与没有配对图的分享完全不受影响。
--
-- 加成性变更 + 幂等：IF NOT EXISTS，可重复执行。

ALTER TABLE hygiene_wecom_shares
    ADD COLUMN IF NOT EXISTS "extra_capture_id" TEXT NOT NULL DEFAULT '';
