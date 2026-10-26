-- 0038_normalize_positions.sql
--
-- 岗位收敛（2026-10-26 用户裁定）：删掉三个不是岗位的名字 ——
--   「明档」      → 明档1（旧写法，没有编号的笼统叫法）
--   「熟笼冻品」  → 熟笼  （它是品类不是岗位；冻品归熟笼档做）
--   「西饼凉菜」  → 西饼  （同上）
--
-- 三组新旧值的档口映射本来就相同（mingdang1 / shulong / xibing），所以这次归并
-- **不改变任何备货量的归属**，只把名字收敛成一套。
--
-- 表与列：备货品主数据（prep_items.position）与换算规则（semi_finished_rules.position）
-- 都存岗位名，一起改。幂等。

UPDATE prep_items SET position = '明档1' WHERE position = '明档';
UPDATE prep_items SET position = '熟笼'  WHERE position = '熟笼冻品';
UPDATE prep_items SET position = '西饼'  WHERE position = '西饼凉菜';

UPDATE semi_finished_rules SET position = '明档1' WHERE position = '明档';
UPDATE semi_finished_rules SET position = '熟笼'  WHERE position = '熟笼冻品';
UPDATE semi_finished_rules SET position = '西饼'  WHERE position = '西饼凉菜';
