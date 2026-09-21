-- 0002：卫生系统的索引补强
--
-- 对应 db_core/schema.py 的 _HYGIENE_INDEX_DEFINITIONS。SQLite 侧这些索引由
-- apply_hygiene_schema() 在启动时自动建；PG 侧按设计不由应用改结构
-- （见 db_core/connection.py::_connect_postgres 的说明），所以补出来给既有库执行。
--
-- 幂等：全部 IF NOT EXISTS，可重复执行。
-- 新装环境执行 0001 之后也执行一次本脚本即可（或把它并进初始化流程）。
--
-- 背景（审查报告 §3.3 / §3.6）：
--   * 原图接口 _original_capture_meta 用 6 路 UNION ALL 按 capture_id 反查来源，
--     这几张表原本都没有 capture_id 索引 → EXPLAIN 显示 SCAN，而所有图片端点默认
--     variant="original"，等于每个图片请求做 6 次全表扫描。
--   * accept_daily 每次都用 (board, item_id, shift, business_date, event_type)
--     判"这一项今天有没有被打回过"。
--   * 看板事件的保留策略按 occurred_at 删旧行，需要单列索引（复合索引前导是 board）。

CREATE INDEX IF NOT EXISTS idx_hygiene_standards_capture
    ON hygiene_standards (capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_daily_submissions_capture
    ON hygiene_daily_submissions (capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_deep_clean_sub_before
    ON hygiene_deep_clean_submissions (before_capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_deep_clean_sub_after
    ON hygiene_deep_clean_submissions (after_capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_fix_tickets_capture
    ON hygiene_fix_tickets (capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_fix_reshoots_capture
    ON hygiene_fix_reshoots (capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_board_events_reject
    ON hygiene_board_events (board, item_id, shift, business_date, event_type);

CREATE INDEX IF NOT EXISTS idx_hygiene_board_events_time
    ON hygiene_board_events (occurred_at);

-- 驳回原因：员工端要能看出"哪里不合格"，否则只看到状态回到待拍、只能原样重拍。
-- 应用本脚本后老数据的 reason 为 NULL，不影响读。
ALTER TABLE hygiene_board_events ADD COLUMN IF NOT EXISTS reason TEXT;

-- 注：没有为 list_fix_tickets 的「status != 已通过」建索引——不等条件用不上索引
-- （SQLite 实测 EXPLAIN 仍是 SCAN），加了只会增加写入成本。要提速得把查询改成
-- 正面枚举状态。
