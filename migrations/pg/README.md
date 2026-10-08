# PostgreSQL schema

`0001_initial_schema.sql` 是**冻结**的初始 schema（全部建表语句 + 多租户 `tenant_id`）。
它当年由 `scripts/archive/sqlite_to_pg_schema.py` 从 SQLite schema 生成，现在生成器和
SQLite schema 本身都已随 ADR 0089 退役——**不要再重新生成 `0001`**，此后的结构变更一律
走下面的增量脚本（现在 `db_core/schema.py` 只剩表名清单，DDL 只在这里）。

应用：

```bash
psql -d luyun -v ON_ERROR_STOP=1 -f migrations/pg/0001_initial_schema.sql
```

⚠️ 该脚本是 `DROP TABLE` + `CREATE TABLE`，**会清空目标库**，只能用于初次建立。

同一批脚本也用于测试：`tests/conftest.py` 在会话开始时按序号把 `migrations/pg/*.sql`
全部应用到专用测试库 `luyun_test`（`0001` 是 bootstrap-only，所以只在空库上跑）。

## 增量迁移（既有库）

`0001` 之后新增的索引/列放在带序号的小脚本里，全部写成幂等 DDL（`IF NOT EXISTS`），
可重复执行。

**推荐做法：更新作业自动应用**——「系统更新」的 Update Job 在 `installing`（已切到新树）
之后、`restarting` 之前有一个 `applying_migrations` 阶段，用**新树自己的**
`scripts/apply_db_migrations.py` 把待应用脚本跑完（迁移 SQL 随发行包下发，所以只有新树里
的这份代码读得到它们——作业进程本身跑的是旧树代码，见 ADR 0096）。没有待应用项时该阶段跳过，
失败则更新作业失败并回滚代码树。

**兜底：Admin 界面手工应用**——「系统更新」区块里有一块「数据库迁移」，会列出待应用的脚本
并支持一键应用（`GET /api/db-migrations` 列出待应用脚本，`POST /api/db-migrations/apply`
应用）。升级到第一个带 `applying_migrations` 的发行包时作业还是旧代码，那一次走这里；需要单独
补应用时同样走这里。两条路用的是同一套 `services/db_migrations` 逻辑与同一张
`schema_migrations` 记录表，所以发版时不必记得敲 psql，也不会出现「代码升了、schema 没升」
这种只在运行期才暴露的错位。

想手工应用也可以：

```bash
psql -d luyun -v ON_ERROR_STOP=1 -f migrations/pg/0002_hygiene_indexes.sql
```

| 脚本 | 内容 | 不执行的后果 |
|---|---|---|
| `0002_hygiene_indexes.sql` | 卫生系统 8 个索引（capture_id 反查、驳回判据、事件保留）+ `hygiene_board_events.reason` | 不报错，但原图接口退化成每请求 6 次全表扫描；驳回原因功能降级为不显示 |
| `0003_hygiene_board_ticket.sql` | `hygiene_board_events.ticket_id` 列 + `idx_hygiene_board_events_ticket` 索引（整改驳回的关联键） | 员工端看不到整改单被驳回，也拿不到驳回原因 |
| `0004_logs.sql` | 日志表 `logs` 进 PostgreSQL（SQLite 的 `data/logs.db` 已退场） | 日志写入全部失败（`relation "logs" does not exist`），`/logs` 页面与 `GET /api/logs/*` 无数据 |
| `0005_scheduling.sql` | 排班系统第一批表：`staff_shifts`（班次表，含白班/夜班两行）、`staff_assignments`（排出来的结果）、`scheduling_rules`（一人一条轮转规则） | 排班页面与 `/api/scheduling/*` 全部报错（`relation "staff_shifts" does not exist`），店长打不开月历 |
| `0006_scheduling_zone_defaults.sql` | `scheduling_zone_defaults`：每人每班次一个固定工作区（区名单仍在 `hygiene_zones`，不搬表） | 店长在排班页配不了工作区，排出来的行 `zone_id` 恒为空 |
| `0007_scheduling_overrides.sql` | `scheduling_overrides`：单日覆盖（某人某天跟规则不一样的那一天，班次+工作区整天快照） | 店长改某天（改班次/改成休/换区）与改规则都 503 并点名这个文件（展开要先读覆盖表）；月历与当日接口照常 200，只是「这天被改过」的青点永远不会出现（标记数的是 `staff_assignments.source`） |
| `0008_scheduling_requests.sql` | `scheduling_requests`：请假申请（一次申请一条，同一个人可以有多条，`kind`/起止日/状态/事由），批准后那几天写成 `kind=leave` 的单日覆盖 | 员工端提不了假、看不了自己的申请，店长的「待办」页 503 并点名这个文件；月历、当日、`/me` 与改某天照常工作（那几条读路径根本不查这张表：请假标记来自 0007 的覆盖记录，读不到就当没有） |
| `0009_scheduling_swap.sql` | `scheduling_requests."peer_employee_id"` 列（换班跟谁换）+ `idx_scheduling_requests_peer` 索引 + 未落定换班的局部唯一索引 `idx_scheduling_requests_swap_once`（同一对同一天只挂一条）；换班与请假共用 0008 那张表，靠 `kind` 分开 | 员工提不了换班（**请假照常**：读路径捕到缺列就按「没有换班」降级，写路径才 503 并点名这个文件）；店长「待办」里不会再出现**新的**换班卡 —— 这一列不在、表里却已经排上队的换班，会让那期间整页待办 503 并点名这个文件（看着像请假也打不开）。把列补回来页面就照常开：那一行的对方已经随列丢了（`DROP COLUMN` 连值一起走），那张卡补不回来 —— 渲染时会跳过它并记一条日志，不会拿空值当「对方」写排班；新提的换班照常走完 |
| `0010_shift_duty_slot.sql` | `staff_shifts."duty_slot"` 列（`day`/`night`/NULL）：这条班次的人做哪一档卫生日常检查，按**班次 id** 认而不是按名字（票 10）。回填现有的「白班」「夜班」两条 | 排班页面与 `/api/scheduling/*` 报 503 并点名这个文件（`list_shifts` 要读这一列，缺列就走 `not_migrated` 那条出口）；同时卫生的员工端交不了任何日常检查（公共层那个只读入口捕到缺列就按「还没接上」降级 —— 员工看到的是「今天没有要交的日常」，不是 500）。**这一条要先应用再看页面** |
| `0012_wecom_hygiene_feed.sql` | `wecom_push_webhooks."hygiene_feed"` 列：把某个企业微信群标成「卫生群」，卫生提醒与验收照片只发它 | 「企微推送」页的地址列表变空（查询捕到缺列就按空列表返回），编辑 / 停用报「webhook 不存在」；卫生侧同样挑不出群 —— **卫生消息一条都不发** |
| `0013_hygiene_wecom_shares.sql` | `hygiene_wecom_shares`：验收照片进群的分享记录（幂等键 + 发送状态 + 重试计数） | **验收照常成功**（代码侧探测到缺表会降级跳过推送，不会让验收事务回滚），但验收照片一张都发不出去，日志里只有一行「未建（迁移 0013 未应用）」 |
| `0014_hygiene_share_pair_image.sql` | `hygiene_wecom_shares."extra_capture_id"` 列：专项前后对照的「前」那张图（主列存「后」），发送时现拼成一张左前右后的对照图 | flush 的查询要读这一列 —— 缺列时**所有**验收照片都发不出去（每轮 flush 报一次错，日志可见）；验收本身照常成功 |
| `0015_hygiene_employee_admin_caps.sql` | `hygiene_employees."admin_caps"` 列：员工的管理能力逐项开关（JSON 数组文本），并按升级前行为回填 `permission = '管理员'` 那三项目 | 花名册里所有员工都变成「没有任何管理能力」（判定以 `admin_caps` 为准，`permission` 那一列不再作数） |
| `0016_wecom_push_subscriptions.sql` | 推送订阅的新模型：`wecom_push_subscriptions`（内容类型 × 渠道/群组）、`wecom_channel_groups`、`wecom_channel_group_members`、`wecom_push_outbox`（队列表兼发送记录），按**三条规则**回填现有收件人，并把 `wecom_push_logs` / `hygiene_wecom_shares` 的历史行搬进 `wecom_push_outbox` | 新代码读不到任何订阅 → **一条推送都发不出去**（卫生提醒、验收照片、采集告警、日报全停）；发送记录页空 |
| `0017_wecom_outbox_sending_at.sql` | `wecom_push_outbox."sending_at"` 列：这一行**进入发送的时刻**，也是「卡在 sending」的兜底判据（进程在 `mark_sending` 之后退出时，留给下一次派发把行捞回来） | 兜底查询捕到缺列 → 卡在 `sending` 的行**永远捞不回来**（就是票 03 返工修的那个缺口）；`mark_sending` 那条 UPDATE 也会整条落空（行留在 `pending`，投递照常，只是状态机少跳一步） |
| `0018_wecom_push_jobs_topic.sql` | 推送任务不再持有收件人：`wecom_push_jobs."topic_id"`（内容类型，按旧 `push_type` 回填）与 `"params_json"`（参数，从旧 `date_range_mode` / `station` / `schedule_time` 搬进来）两列，并给旧列 `webhook_id` 补默认值 | 任务列表与详情查询整条落空（缺列 → 页面「定时任务」空、`dispatch_due_jobs` 一条都不入队，**定时推送全停**）；新建任务也会失败（`webhook_id` 是 NOT NULL 而新代码不再写它）。收件人本身不受影响（那由 0016 的订阅决定） |
| `0019_wecom_push_audit.sql` | 配置变更历史 `wecom_push_audit`（只追加：时间 / 操作人 / 动作 / 对象 / 前后值快照）+ 两条按时间倒序的索引 | **配置变更不报错、只是不留痕**：审计写失败被吞成一行日志（`db_core/wecom_audit_repo.py`），所以页面「变更历史」tab 一直空、「谁在什么时候把这个群停用了」查不出来。渠道 / 订阅 / 任务本身照常工作 |
| `0020_hygiene_employee_profile.sql` | 员工档案四列 `hygiene_employees."id_card_no"` / `"health_cert_date"` / `"base_salary"` / `"hire_date"`（全可空、无默认值）+ 身份证号的部分唯一索引 `idx_hygiene_employees_id_card`（`tenant_id, id_card_no`，`WHERE id_card_no IS NOT NULL`） | **所有读员工行的路径整条报错、页面 500**（缺列：`column e.health_cert_date does not exist`）—— 不只花名册，员工登录、审批、员工端「我的」与排班读名单都要 `SELECT` 这几列。这里**刻意不做降级**：读不到档案时把它当"没填"渲染，会让所有人静默变成「待补」、底薪空着也能批准，比报错更难查。应用后即恢复。健康证到期日是派生值、不落库（ADR 0098） |

| `0021_overtime_entries.sql` | 加班与补钟台账 `overtime_entries`（票 01）：一条 = 一个**自然日** + 一个**带符号的半小时数**（`half_hours`，`CHECK <> 0`，±24 = ±12 小时）+ 必填事由 + 状态机（`pending` / `approved` / `rejected` / `cancelled` / `voided`），配三条索引（待办 / 我的记录 / 按日期统计） | 「加班与补钟」页整页 503 并点名这个文件（`OvertimeLedger` 的每个方法都把缺表异常转成 `not_migrated`）：员工提交、撤回与月度净时长都不可用。排班 / 卫生 / 员工登录不受影响（那几条路不碰这张表） |

**`0001` 带 `luyun:bootstrap-only` 标记**：它含 `DROP TABLE`，只用于初次建库，
Admin 面板靠这行标记把它永久排除在待应用之外（`test_db_migrations.py` 会校验这个标记，
别删）。

**为什么不在启动时自动补**：`_connect_postgres` 明确不承担结构变更（见其 docstring），
「schema 是谁改的」要可追溯。所以**改了结构就必须出脚本**，没有「重启即自愈」这条捷径。

**发布新版时的检查清单**：如果本次新增/改动了表或索引，就要同时出一个 `000N_*.sql`
（只做加成性变更）并**提交**——发行包按 `git archive HEAD` 打包，没提交的脚本不会进包，
门店也就应用不到。

运行期的 SQL 仍按 `?` 占位符与 `rowid` 的老写法书写，由驱动边界的方言层转成 PostgreSQL
方言——见 `db_core/backend/dialect.py`（SQLite 已退场，方言层保留）。

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

**回滚**：迁移脚本对源库全程只读（`mode=ro`），源库未被改动，所以这一步本身不做破坏性
写入。但**没有「把 `DATABASE_BACKEND` 改回 `sqlite`」这条回滚路**——SQLite 已退场
（ADR 0089），那样改只会让应用启动失败。切库后的数据回滚用 PG 快照（`pg_restore`，
见 `deploy/README.md` §10.4/§10.5）；`data/app.db` 只是历史快照，必要时用它重新灌一次
PG。观察期结束前不要删源库。

迁移脚本本身**不做增量同步**——它是停机窗口内的一次性全量搬运。若将来需要
零停机，要另做双写或逻辑复制，不在此脚本范围内。


## PG 下的备份与导出

**管理后台「备份中心 → 导出备份」打的是整库快照**：`.luyunbak` 的业务数据成员是
`app.pgdump`（`pg_dump --format=custom`），恢复时走 `pg_restore --clean` 整库覆盖——
**没有合并导入**，导入面板只提供「覆盖恢复」。

手工路径（宿主机冷备、页面不可用时）见
[`deploy/README.md` 第 10.4 节](../../deploy/README.md)。

**遗留的「导出 DB / 导入 DB」已收口**：管理后台的 `/api/admin/export/db` 现在打的
也是整库 `pg_dump`（下载名 `luyun-export-<时间戳>.pgdump`，与 `.luyunbak`、更新前
备份、`deploy/backup.sh` 是同一条路径），不再产出 SQLite 时代的 `.db` 逐表导出——
`PgConnection.backup`、`TABLE_DEDUP_KEY` 与 `db_core/backend/sqlite_export.py` 都随
SQLite 一起退场（ADR 0089）。`/api/admin/import/{preview,execute}` 固定返回 400 +
指引，因为逐表合并导入没有对应物：**恢复只剩 `.luyunbak` 整库覆盖这一条路**。

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
