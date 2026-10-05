-- 员工的管理权限开关（2026-10-05 用户裁定：由超级管理员逐项放权）
--
-- 背景：原来 `hygiene_employees.permission` 只有两档（`普通员工` / `管理员`），
-- 一给就是三项能力（日常验收、专项验收、整改单），而且只在员工端手机页里生效。
-- 现在改成**逐项开关**：十项能力各一个键，超级管理员在花名册里勾给谁就是谁。
--
-- 取值（能力键，见 `services/identity/capabilities.py`）：
--   daily_review  日常验收      deep_review  专项验收     fix       整改单（开单 + 验收）
--   attire        仪容仪表      standard     标准图管理   zone      工作区与检查项
--   roster        花名册与排班  boards       红黑榜与教材 clock     时限设置
--   data          数据与归档
--
-- 存成 JSON 数组文本（与 `permission` 一样是 TEXT，不加新表）：空数组 = 没有任何管理能力。
-- 回填按**升级前的行为逐字对齐**：`permission = '管理员'` 的人拿头三项，普通员工留空。
-- `permission` 这一列保留（花名册与员工页仍拿它显示"管理员 / 普通员工"这一个人话标签），
-- 但**判据一律以 `admin_caps` 为准** —— 两套并存时以开关为准，避免"标签给了、开关没给"。

ALTER TABLE hygiene_employees
    ADD COLUMN IF NOT EXISTS admin_caps TEXT NOT NULL DEFAULT '[]';

UPDATE hygiene_employees
   SET admin_caps = '["daily_review","deep_review","fix"]'
 WHERE permission = '管理员'
   AND (admin_caps IS NULL OR admin_caps = '[]');
