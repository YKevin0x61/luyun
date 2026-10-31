-- 0043_shift_color.sql
--
-- 班次配色（`.scratch/scheduling-shift-color/spec.md`）：给 `staff_shifts` 加一个
-- **语义色键**，周表/月历的格子按它上色，店长在班次表页自己选。
--
-- 为什么落库、为什么存键而不是存色值：
--   * 以前颜色由前端 `SHIFT_TONES` 按「班次在列表里的位置」取，停用或新增一个班次，
--     全店格子的颜色整体错位；同一屏上 `mint`/`aqua` 还共用同一个底色、浅色主题下
--     两个色值是同一个蓝 —— 店长分不出「早班」和「夜班」。
--   * 存色键（`moss` / `mint` / …）而不是 hex：店长选的是**档位**，具体色值在
--     `theme.workbench.css` 里，深浅两主题共用同一套值，对比度由前端守（实测
--     任意两档 CIE76 ΔE ≥ 26.7、文字对比度 ≥ 4.80:1）。存 hex 就把这两条交给了店长。
--
-- 语义：
--   * 8 个真实色键：moss/mint/tide/sky/pine/rose/grass/lilac（前 6 个是主色，
--     后 2 个只在前 6 个都被占用时才启用）。
--   * `neutral` 是**哨兵**：可重复、不占唯一额，用于「第 9 个启用班次」与读取端兜底。
--
-- 唯一性：**启用中的班次不许撞色**。部分唯一索引（`WHERE is_active = 1`），因此停用的
-- 班次保留自己的色但不占额 —— 停用一个班次后，它的色可以给新班次用；重新启用时若那档
-- 已经给别人，写入侧报 `shift_color_taken` 让店长先改色（`staff_shifts` 没有别的可见约束，
-- 这条判断写在 `services/scheduling/store.py` 的 `_update_shift_row` 里）。
--
-- 回填：存量班次按「启用的在前、再按 sort_order、id」依次拿主色，顺序与今天页面上的
-- 顺序一致，店长上线当天就能对上眼。第 9 条起保持 `neutral`。回填只在 `color_key`
-- 还是 `neutral` 且那一档没被占用时写，可重复执行。
--
-- 幂等：IF NOT EXISTS + 有条件的 UPDATE。纯加成性变更（加列带默认值 + 建索引），
-- 应用前后读接口行为完全一样（只是多一个字段）。

ALTER TABLE staff_shifts ADD COLUMN IF NOT EXISTS "color_key" TEXT NOT NULL DEFAULT 'neutral';

WITH ranked AS (
    SELECT id,
           (ARRAY['moss', 'mint', 'tide', 'sky', 'pine', 'rose', 'grass', 'lilac'])
           [row_number() OVER (ORDER BY is_active DESC, sort_order ASC, id ASC)] AS key
      FROM staff_shifts
     WHERE color_key = 'neutral'
)
UPDATE staff_shifts AS s
   SET color_key = r.key
  FROM ranked AS r
 WHERE s.id = r.id
   AND r.key IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM staff_shifts AS used
        WHERE used.color_key = r.key AND used.is_active = 1
   );

CREATE UNIQUE INDEX IF NOT EXISTS "uq_staff_shifts_active_color"
    ON staff_shifts (tenant_id, color_key)
 WHERE is_active = 1 AND color_key <> 'neutral';
