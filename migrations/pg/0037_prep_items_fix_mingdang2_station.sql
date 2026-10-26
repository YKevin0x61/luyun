-- 0037_prep_items_fix_mingdang2_station.sql
--
-- 修一个岗位映射漏项：前端岗位下拉里有「明档2」，后端 `POSITION_STATION_MAP`
-- 里没有 → 选了这个岗位的备货品，档口映射查不到、存成空串。
-- 库里已经空了 3 个（牛杂 / 猪脚姜 / 鱼皇料），备货页把它们归到「未分类」。
--
-- 本次：映射补上（代码侧），这里把已经存空的那几行补回档口。幂等。

UPDATE prep_items
SET station = 'mingdang2'
WHERE position = '明档2'
  AND COALESCE(station, '') = '';
