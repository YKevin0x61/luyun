-- 0036_semi_rules_backfill_prep_item.sql
--
-- 把配方系数接到备货品主数据（接 0035 的加列）。0035 只加列，**回填放在这一次数据迁移里**
-- 完成：门店只会点「系统更新 → 数据库迁移」，不会去跑开发机上的 Python 脚本
-- （`.scratch/prep-rules/spec.md` §3）。之前实测过一次：0035 应用了、`prep_item_id`
-- 全是 NULL，页面把每条规则都标成「未对齐」。
--
-- 三件事，全部幂等，不删任何行：
--   1. 两条名字漂移按业务判断对齐（鱼皇粥料→鱼皇料、姜汁双皮奶→双皮奶）；
--   2. 补齐「规则在用、备货品主数据里没有」的半成品 —— **从规则表自身派生**，不硬编码
--      这 13 个名字，别的门店的新菜也能补上；
--   3. 回填 `prep_item_id`：先按 (名字, 单位) 精确匹配，再按名字兜底（单位不同也算，
--      但**不改单位** —— 单位漂移进核对清单，让人确认，脚本不替人做数）。
--
-- 可重复执行：每一步都带 NOT EXISTS / IS NULL 守卫。

-- ① 名字漂移：规则名 → 备货品主数据里已有的名字（主数据不动）
UPDATE semi_finished_rules
SET semi_name = '鱼皇料'
WHERE semi_name = '鱼皇粥料'
  AND EXISTS (SELECT 1 FROM prep_items WHERE item_name = '鱼皇料');

UPDATE semi_finished_rules
SET semi_name = '双皮奶'
WHERE semi_name = '姜汁双皮奶'
  AND EXISTS (SELECT 1 FROM prep_items WHERE item_name = '双皮奶');

-- ② 补齐缺失的备货品：一条规则引用、但主数据里没有 (名字, 单位) 的，就地建一个。
--    position/category 取规则的；station 走 POSITION_STATION_MAP 同一张表；
--    kind 按 category 推断（半成品→made、原料→bought），推错可在备货页改。
INSERT INTO prep_items
    (tenant_id, item_name, station, position, category, unit,
     shelf_life_hours, lead_time_hours, min_batch_qty, safety_stock_ratio,
     active, notes, created_at, updated_at, kind)
SELECT DISTINCT ON (r.tenant_id, r.semi_name, r.unit)
    r.tenant_id,
    r.semi_name,
    CASE COALESCE(r.position, '')
        WHEN '熟笼' THEN 'shulong'
        WHEN '熟笼冻品' THEN 'shulong'
        WHEN '馅档' THEN 'shulong'
        WHEN '案板' THEN 'shulong'
        WHEN '西饼' THEN 'xibing'
        WHEN '西饼凉菜' THEN 'xibing'
        WHEN '肠粉' THEN 'changfen'
        WHEN '明档' THEN 'mingdang1'
        WHEN '明档1' THEN 'mingdang1'
        WHEN '煎炸' THEN 'jianzha'
        ELSE ''
    END,
    COALESCE(r.position, ''),
    COALESCE(r.category, ''),
    COALESCE(r.unit, ''),
    24, 0, 0, 0.15,
    1,
    'backfill_from_rules_0036',
    to_char(now() AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '+08:00',
    to_char(now() AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '+08:00',
    CASE WHEN r.category = '原料' THEN 'bought' ELSE 'made' END
FROM semi_finished_rules r
WHERE COALESCE(r.semi_name, '') <> ''
  -- 按**名字**判重、不看单位：规则写「份」而主数据只有「个」是单位漂移，
  -- 不是「两样东西」。这里再建一个「虾饺/份」会让备货清单出现两个虾饺；
  -- 单位不一致交给页面标 unit_drift，让人确认。
  AND NOT EXISTS (
      SELECT 1 FROM prep_items p
      WHERE p.item_name = r.semi_name
  )
ORDER BY r.tenant_id, r.semi_name, r.unit;

-- ③a 精确回填：名字 + 单位都一致
UPDATE semi_finished_rules r
SET prep_item_id = p.id
FROM prep_items p
WHERE p.item_name = r.semi_name
  AND p.unit = COALESCE(r.unit, '')
  AND r.prep_item_id IS NULL;

-- ③b 名字兜底：同名、单位不同也算接上（只写 id，不改 unit）
UPDATE semi_finished_rules r
SET prep_item_id = (
    SELECT p.id FROM prep_items p
    WHERE p.item_name = r.semi_name
    ORDER BY p.id
    LIMIT 1
)
WHERE r.prep_item_id IS NULL
  AND COALESCE(r.semi_name, '') <> ''
  AND EXISTS (SELECT 1 FROM prep_items p WHERE p.item_name = r.semi_name);
