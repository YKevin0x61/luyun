-- 0010：班次挂上「卫生档位」（票 10）
--
-- 背景：卫生的日常检查分白班档、夜班档两套（`hygiene_zones.day_shift/night_shift` 是开关，
-- `hygiene_daily_instances.shift` 存的就是「白班」「夜班」两个字符串）。票 10 起，员工当天
-- 上哪一档由**排班结果**决定（`staff_assignments.shift_id`），员工不再自己选。
--
-- 但排班的班次是**数据**：店长能改名、能加第三个（票 11）。按名字对齐的话，把「夜班」改成
-- 「晚班」的那天，所有夜班的人就都交不了日常了 —— 票 10 的转出注记专门点了这个坑
-- （「改个名就静默错配，是这一票最容易踩的坑」）。所以把「这条班次属于卫生的哪一档」
-- 记在班次行上，按 **id** 认：
--
--   duty_slot = 'day'    排到这条班次的人，做白班档的日常检查
--               'night'  夜班档
--               NULL     不出日常
--
-- 新加的班次默认 NULL：店长没说过它是哪一档，就不替他决定（那天的人交不了日常，
-- 页面上会说去找店长 —— 比猜错一档、把白班的检查单发给夜班人要好）。
--
-- 这里**不建唯一约束**：几条班次同属一档是合法的（「早班」「白班」都算白班档），卫生按
-- 排到的那条班次的档位取日常。也不建 CHECK：非法值在卫生那一侧按「不认识的档」处理，
-- 结果是「今天没有日常可交」——安全的降级，不值得为它挡下一次迁移。
--
-- 回填：把现有的默认两条按名字认一次，让装好的店不用先去点两下。**只认这两个名字**：
-- 店主要是早把「白班」改了名，这一句认不出来 —— 那种店本来就已经断了（老代码按名字对齐），
-- 去班次表页点一下档位即可。
--
-- 幂等：IF NOT EXISTS + 只回填为空的那些行，可重复执行。

ALTER TABLE staff_shifts
    ADD COLUMN IF NOT EXISTS "duty_slot" TEXT;

UPDATE staff_shifts SET duty_slot = 'day'
 WHERE duty_slot IS NULL AND name = '白班';

UPDATE staff_shifts SET duty_slot = 'night'
 WHERE duty_slot IS NULL AND name = '夜班';
