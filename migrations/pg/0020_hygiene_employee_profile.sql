-- 0020：员工档案的四项新字段 + 身份证号的部分唯一索引
--
-- 背景（2026-10-07 用户裁定，见 `.scratch/roster-redesign/design.md`）：
-- 花名册从「手机号 + 姓名 + 职位」扩成一份能对账的档案 —— 新入职的人由员工自己
-- 在注册页填**身份证号**与**健康证办理日期**（两项必填），超级管理员再补**底薪**与
-- **入职日期**；四项齐了才允许批准（门槛在服务层/接口层，不在这一层）。健康证到期
-- 提醒要显示在花名册行、员工端「我的」与超管首页待办三处。
--
-- 四列全部**可空、无默认值**（加成性变更）：
--   * `id_card_no`      TEXT  18 位身份证号，明文（用户明确选择：便于核对证件）。
--                              哨兵值是 NULL —— 服务层把空串归一成 NULL 再写，
--                              所以「没填」在库里只有一种表示。
--   * `health_cert_date` TEXT  健康证**办理日期** YYYY-MM-DD。**到期日不落库**：
--                              有效期 = 办理日期 + 12 个月，是派生值（ADR 0098 的口径），
--                              只有 `services/identity/profile.py` 一处算，改规则只改那里。
--   * `base_salary`     BIGINT **整数**元/月。库里别的金额列是 DOUBLE PRECISION，
--                              但那是两位小数的销售金额；这一列按契约就是整数元
--                              （0–999999，界面 type=number step=1），存成整数类型
--                              才不会在下游把 6000 读成 6000.000000000001。
--   * `hire_date`       TEXT  入职日期 YYYY-MM-DD（允许将来：提前建档）。
--
-- 日期列一律 TEXT（ISO 字符串）：与本库既有的日期列同构（`hygiene_employees.created_at`、
-- 各表的 `business_date` 都是 TEXT），见 `migrations/pg/README.md` 的「已知取舍」——
-- 迁移到 DATE/TIMESTAMPTZ 是独立议题，不跟着这次档案改动走。
--
-- `created_at` 已经存在（`0001` 的 `hygiene_employees`），这次只是把它放进返回，
-- **不动这一列**。
--
-- 幂等：IF NOT EXISTS，可重复执行。依赖 0001（表在）。

ALTER TABLE hygiene_employees
    ADD COLUMN IF NOT EXISTS "id_card_no" TEXT;

ALTER TABLE hygiene_employees
    ADD COLUMN IF NOT EXISTS "health_cert_date" TEXT;

ALTER TABLE hygiene_employees
    ADD COLUMN IF NOT EXISTS "base_salary" BIGINT;

ALTER TABLE hygiene_employees
    ADD COLUMN IF NOT EXISTS "hire_date" TEXT;

-- 同一个人只能建一份档：同一张身份证号不许注册两次（票面要求「同号重复注册 → 400」）。
-- * 局部索引（`WHERE id_card_no IS NOT NULL`）：存量行、以及还没填身份证的行（NULL）
--   不在索引范围内，所以这条索引在升级当场一定建得起来，也不会互相打架。
-- * 带 `tenant_id`：多店数据落库后身份证号只在店内唯一（与 0001 的 `UNIQUE (tenant_id, phone)`
--   同一口径）。
-- 服务层在写之前先查一次（要给中文人话），这条索引是并发下的最后一道闸：撞上它就报
-- 「该身份证号已建档」，不会落成 500。
CREATE UNIQUE INDEX IF NOT EXISTS idx_hygiene_employees_id_card
    ON hygiene_employees (tenant_id, id_card_no)
    WHERE id_card_no IS NOT NULL;
