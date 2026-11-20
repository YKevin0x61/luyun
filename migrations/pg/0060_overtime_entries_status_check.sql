-- 0060 加班登记的状态词表钉进库
--
-- 抵扣表在 0042 就带了 CHECK，登记表一直只靠服务层；两条路合成一台状态机后
-- （ADR 0153）两边应该同一口径。加成性变更：已有行都在这五个值里。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'overtime_entries_status_check'
          AND conrelid = 'overtime_entries'::regclass
    ) THEN
        ALTER TABLE overtime_entries
            ADD CONSTRAINT overtime_entries_status_check
            CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled', 'voided'));
    END IF;
END
$$;