-- 0003：整改驳回的关联键
--
-- 对应 db_core/schema.py 的 hygiene_board_events.ticket_id。
--
-- 背景（复审 A5）：专项与整改的驳回此前员工端完全看不到——专项驳回的事件里没有
-- item_id（或留空），整改驳回更是连可关联的键都没有（整改单 id 与检查项不是一套
-- 编号，塞进 item_id 会让 daily 的驳回判据误判）。加一列存 ticket_id，员工端才能
-- 认出"这一张整改单被打回过"，并带上原因。
--
-- 幂等：IF NOT EXISTS，可重复执行。

ALTER TABLE hygiene_board_events ADD COLUMN IF NOT EXISTS ticket_id BIGINT;

-- 员工端按 (board, event_type, ticket_id, business_date) 反查当天被驳回的整改单。
CREATE INDEX IF NOT EXISTS idx_hygiene_board_events_ticket
    ON hygiene_board_events (ticket_id);
