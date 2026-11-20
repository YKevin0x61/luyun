-- 0059：照片台账扩到 10 源，补齐后四路的 capture 索引
--
-- 背景：services/hygiene/captures_ledger.py 把「这张图属于哪条记录」的
-- 查询从 6 路 UNION ALL 扩到 10 路（多了教材左右图、仪容实拍、仪容标准图）。
-- 0002 只给前 6 路建了索引；后四张表按 capture_id 反查仍会全表扫描，
-- 而这些端点正是图片请求的默认路径（variant=original）。
--
-- 幂等：全部 IF NOT EXISTS。仅加成性变更，不动既有列。

CREATE INDEX IF NOT EXISTS idx_hygiene_teaching_left_capture
    ON hygiene_teaching_examples (left_capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_teaching_right_capture
    ON hygiene_teaching_examples (right_capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_attire_shots_capture
    ON hygiene_attire_shots (capture_id);

CREATE INDEX IF NOT EXISTS idx_hygiene_attire_standard_capture
    ON hygiene_attire_standard (capture_id);
