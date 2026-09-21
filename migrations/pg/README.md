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

## 增量迁移（既有库）

`0001` 之后新增的索引/列放在带序号的小脚本里，全部写成幂等 DDL（`IF NOT EXISTS`），
可重复执行。

**推荐做法：用 Admin 界面应用**——「系统更新」区块里有一块「数据库迁移」，会列出
待应用的脚本并支持一键应用，应用记录写进库里的 `schema_migrations` 表，随时能看出
当前到哪一版（`GET/POST /api/db-migrations`）。这样发版时不必记得敲 psql，也不会
出现「代码升了、schema 没升」这种只在运行期才暴露的错位。

想手工应用也可以：

```bash
psql -d luyun -v ON_ERROR_STOP=1 -f migrations/pg/0002_hygiene_indexes.sql
```

| 脚本 | 内容 | 不执行的后果 |
|---|---|---|
| `0002_hygiene_indexes.sql` | 卫生系统 8 个索引（capture_id 反查、驳回判据、事件保留）+ `hygiene_board_events.reason` | 不报错，但原图接口退化成每请求 6 次全表扫描；驳回原因功能降级为不显示 |

**`0001` 带 `luyun:bootstrap-only` 标记**：它含 `DROP TABLE`，只用于初次建库，
Admin 面板靠这行标记把它永久排除在待应用之外（`test_db_migrations.py` 会校验这个标记，
别删）。

**为什么不在启动时自动补**：`_connect_postgres` 明确不承担结构变更（见其 docstring），
「schema 是谁改的」要可追溯。SQLite 侧没有这个约束——它由 `apply_hygiene_schema()` /
`migrate_hygiene_columns()` 在启动时补齐，所以两边行为不对称是**有意**的。

**发布新版时的检查清单**：如果本次改动了 `db_core/schema.py` 的 `_HYGIENE_TABLE_SCHEMAS`
或 `_HYGIENE_INDEX_DEFINITIONS`，就要同时出一个 `000N_*.sql` 并在发布说明里写清楚——
PG 部署不会自动获得这些变更。

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

## 停机迁移流程

前置：目标 PG 库已应用 `0001_initial_schema.sql`；已确认可停机（多店前提下的
一次性切换）。

```bash
# 1. 停应用（迁移期间源库必须静止，否则会漏掉增量）
systemctl stop luyun          # 或 docker compose -f deploy/docker-compose.yml stop luyun

# 2. 备份源库——回滚靠它
sqlite3 data/app.db ".backup 'backups/pre-pg-migration.db'"

# 3. 预演：只统计两侧行数与依赖顺序，不写库
.venv/bin/python scripts/archive/migrate_sqlite_to_postgres.py --dry-run

# 4. 执行（每表 TRUNCATE + COPY，可重复执行；结束后自动重置 identity 序列）
.venv/bin/python scripts/archive/migrate_sqlite_to_postgres.py --apply

# 5. 切换后端并启动
DATABASE_BACKEND=postgres .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

**冒烟清单**：`/api/healthz` 200 → 后台订单列表有数据 → KDS 厨房屏正常 →
admin 数据浏览器能翻页与编辑一行 → 用原密码能登录后台。

**回滚**：把 `DATABASE_BACKEND` 改回 `sqlite` 重启即可。迁移脚本对源库全程只读
（`mode=ro`），源库未被改动，所以回滚没有数据损失。观察期结束前不要删源库。

迁移脚本本身**不做增量同步**——它是停机窗口内的一次性全量搬运。若将来需要
零停机，要另做双写或逻辑复制，不在此脚本范围内。


## PG 下的备份与导出

**管理后台「备份中心 → 导出备份」在 PG 门店打的是整库快照**：`.luyunbak` 的业务数据
成员是 `app.pgdump`（`pg_dump --format=custom`），恢复时走 `pg_restore --clean` 整库
覆盖——**没有合并导入**（SQLite 那边的 `app.db` 成员才有逐表合并，导入面板在 PG 包上
只提供「覆盖恢复」）。两个方向的包不能互灌：PG 门店的包灌不进 SQLite，SQLite 的包
也灌不进 PG，后端会明确拒绝并说明该用哪种。

手工路径（宿主机冷备、页面不可用时）见
[`deploy/README.md` 第 10.4 节](../../deploy/README.md)。

**配方数据取自当前连接**（PG 里的 `sop_*`），不是 `data/app.db` 那份迁移遗留副本。
两者在切换后就会分叉——源库按上面的流程只读保留，配方却继续在 PG 里改——把遗留
副本当备份用，恢复时会把配方回退到迁移那一刻。


## 已知取舍

- **时间戳保持 `TEXT`**（ISO 字符串）、**金额保持 `DOUBLE PRECISION`**，以兼容
  现有查询代码。代价是拿不到 PG 的日期函数，且金额求和会出现浮点尾差
  （实测 `1593.6` vs `1593.5999999999997`）。迁移为 `TIMESTAMPTZ` / `NUMERIC`
  是独立议题。
- **索引暂不以 `tenant_id` 为前导列**：单店阶段该列恒为 1，前导无选择性收益；
  多店数据落库后按实际执行计划再调。
- 13 个单店唯一约束已收敛为 `(tenant_id, ...)` 复合唯一。
- `admin_user` 的 `CHECK (id = 1)` 已移除（它是「只允许一个管理员」的硬编码来源）。
