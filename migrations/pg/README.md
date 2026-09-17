# PostgreSQL schema

`0001_initial_schema.sql` 由 `scripts/archive/sqlite_to_pg_schema.py` 从当前
SQLite schema 生成（含多租户 `tenant_id`）。重新生成：

```bash
.venv/bin/python scripts/archive/sqlite_to_pg_schema.py \
    data/app.db migrations/pg/0001_initial_schema.sql
```

应用：

```bash
psql -d luyun -v ON_ERROR_STOP=1 -f migrations/pg/0001_initial_schema.sql
```

⚠️ 该脚本是 `DROP TABLE` + `CREATE TABLE`，**会清空目标库**，只能用于初次建立。

运行期的 SQL 仍由代码按 SQLite 方言书写，在驱动边界转换——见
`db_core/backend/dialect.py` 与 ADR 0084。

## 数据迁移注意事项

**导入数据后必须重置 identity 序列。**

用 `copy_records_to_table` 或 `INSERT ... (id, ...)` 显式写入主键时，PG 的
identity 序列**不会跟着推进**。之后任何不带 `id` 的 INSERT 都会从 1 开始生成
主键，直接报：

```
duplicate key value violates unique constraint "orders_pkey"
DETAIL:  Key (id)=(3) already exists.
```

这个坑在本次验证中真实触发过。每次导入数据后跑一次：

```sql
DO $$
DECLARE r RECORD; seq TEXT;
BEGIN
  FOR r IN
    SELECT c.relname FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname = 'public'
      AND EXISTS (SELECT 1 FROM pg_attribute a
                  WHERE a.attrelid = c.oid AND a.attname = 'id' AND a.attnum > 0)
  LOOP
    seq := pg_get_serial_sequence('public.' || quote_ident(r.relname), 'id');
    IF seq IS NOT NULL THEN
      EXECUTE format(
        'SELECT setval(%L, COALESCE((SELECT MAX(id) FROM public.%I), 1))',
        seq, r.relname);
    END IF;
  END LOOP;
END $$;
```

`0001_initial_schema.sql` 只对 `tenants` 做了 setval——建表时其余表都是空的。

## 已知取舍

- **时间戳保持 `TEXT`**（ISO 字符串）、**金额保持 `DOUBLE PRECISION`**，以兼容
  现有查询代码。代价是拿不到 PG 的日期函数，且金额求和会出现浮点尾差
  （实测 `1593.6` vs `1593.5999999999997`）。迁移为 `TIMESTAMPTZ` / `NUMERIC`
  是独立议题。
- **索引暂不以 `tenant_id` 为前导列**：单店阶段该列恒为 1，前导无选择性收益；
  多店数据落库后按实际执行计划再调。
- 13 个单店唯一约束已收敛为 `(tenant_id, ...)` 复合唯一。
- `admin_user` 的 `CHECK (id = 1)` 已移除（它是「只允许一个管理员」的硬编码来源）。
