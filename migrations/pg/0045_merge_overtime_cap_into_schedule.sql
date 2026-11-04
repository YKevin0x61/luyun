-- 0045_merge_overtime_cap_into_schedule.sql
--
-- 加班与补钟审批并入「人事管理」（用户 2026-11-03 裁定）。
--
-- 起因：`admin_caps` 里原来有两个挨着的开关 —— `overtime`（加班与补钟审批）和
-- `schedule`（人事管理）。它们管的是同一批店长在员工端做的同一类事（排班 / 考勤 /
-- 加班审批），花名册上并排两项，超管很容易只勾一项、被勾的人回头发现另一半用不了。
-- 用户裁定：**只保留「人事管理」一项**，加班与补钟审批由它承担。
--
-- 改动（代码侧）：`services/identity/capabilities.py` 删掉 `overtime` 键，
-- `CAPABILITIES` 从十三项变成十二项；`api/overtime.py` 的 `_can_review` 改判
-- `CAP_SCHEDULE`。认不出的键会被 `parse_caps` 丢掉（fail-closed），所以**不迁移的话**，
-- 原来只勾了 `overtime` 的人会静默失去加班审批权 —— 这份脚本就是防这个。
--
-- 口径：把 `admin_caps` 里的 `overtime` **就地换成** `schedule`（已有 `schedule` 的
-- 去重后只留一个），其余键原样保留、原顺序不变。也就是说：
--   * 原来同时勾了 `overtime` + `schedule` 的人：行为不变；
--   * 原来**只**勾了 `overtime` 的人：合并后拿到 `schedule` —— 除了加班审批，还会获得
--     人事管理其余部分的权限（改全店排班、批请假与换班、管班次表、看与改考勤表）。
--     这是「并入」的应有之义；若不想要这个人拿到那些，应用本迁移后到花名册里手工收回。
--
-- 安全性：只动 `admin_caps` 这一列，不改表结构、不删任何行；不在 WHERE 里的行一个字节
-- 都不变。空 / 非法 JSON 的行不参与（`jsonb` 转换失败会让整条 UPDATE 回滚，而不是写坏）。
-- 幂等：`WHERE ... jsonb_exists(..., 'overtime')` 让第二次执行无事可做，可重复运行。
-- （不用 `jsonb ? 'key'` 运算符：这台机器的 SQL 执行层会把 `?` 改写成 `$n` 占位符。）
--
-- 谁读它：`services/identity/capabilities.py` 的 `parse_caps`（认不出的键一律丢掉）。
-- 谁写它：`services/identity/accounts.py` 的 `set_admin_caps`（走 `dump_caps` 归一）。

UPDATE hygiene_employees AS e
   SET admin_caps = (
       SELECT COALESCE(jsonb_agg(mapped ORDER BY ord), '[]'::jsonb)::text
         FROM (
           SELECT DISTINCT ON (mapped) mapped, ord
             FROM (
               SELECT CASE WHEN value = 'overtime' THEN 'schedule' ELSE value END AS mapped,
                      ord
                 FROM jsonb_array_elements_text(e.admin_caps::jsonb)
                      WITH ORDINALITY AS t(value, ord)
             ) AS raw
            ORDER BY mapped, ord
         ) AS dedup
   )
 WHERE e.admin_caps IS NOT NULL
   AND e.admin_caps <> ''
   AND jsonb_exists(e.admin_caps::jsonb, 'overtime');
