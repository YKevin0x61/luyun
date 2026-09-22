# 升级到 0.6.0 操作方案

> **本文是 0.5.19 → 0.6.0 的历史方案。** 0.6.x 之后 SQLite 已退场（PostgreSQL 成为
> 唯一后端，见 [ADR 0089](adr/0089-retire-sqlite-postgres-only.md)）：文中场景 A
> （只升级、继续用 SQLite）**不再适用**，阅读时只把场景 B 的迁移路径当参考；当前
> 做法以 `deploy/README.md` §10 与 `migrations/pg/README.md` 为准。
>
> 适用：云端 **0.5.19**（Docker 或 systemd 部署）→ **0.6.0**
> 相关：ADR 0011（发行包）、ADR 0084（PostgreSQL 后端）、`migrations/pg/README.md`

## 0. 先看结论

| 你的目标 | 需要做什么 | 风险 |
|---|---|---|
| **只升级，继续用 SQLite** | 后台点「系统更新」 | **低**（数据不动，自动备份）|
| **升级 + 切 PostgreSQL** | 升级后跑 `enable_postgres.sh`（Docker 会自动重建镜像）| 中（涉及数据搬迁，有回滚路径）|

**0.5.19 → 0.6.0 没有 schema 变更**，`data/app.db` 可直接被 0.6.0 打开（已实测：42 张表全部一致）。
所以"只升级"这条路是安全的。

---

## 1. 升级前检查清单

```bash
# ① 当前版本与部署形态
curl -s localhost:8000/api/healthz            # 看 version 与 database
docker ps --format '{{.Names}}\t{{.Image}}'   # Docker 形态；或 systemctl status luyun

# ② 磁盘余量（更新会 pip sync，满盘会写坏 .venv）
df -h /opt/luyun      # 官方门槛 UPDATE_MIN_FREE_MB=2048

# ③ 备份当前 SQLite（双保险，别只依赖更新作业的自动备份）
sqlite3 data/app.db ".backup 'backups/pre-0.6.0-$(date +%Y%m%d_%H%M%S).db'"

# ④ 确认业务低峰（更新会重启服务，KDS 会短暂断连）
```

> **更新环境自检**（后台「系统更新」页）会给红绿灯。磁盘、重启能力、部署目录、业务库
> 连通性（0.6.0 新增）任一不过，先解决再点应用更新。

---

## 2. 场景 A：只升级（保持 SQLite）

### 2.1 Docker 部署

1. 后台 →「系统更新」→ 版本检测 → 选定 `v0.6.0` → 应用更新
2. 观察阶段：`backing_up → fetching_bundle → installing → syncing_deps → restarting`
3. `syncing_deps` 会因 `requirements.txt` 变化（新增 `asyncpg` / `redis`）触发一次 `pip install`
4. 完成后冒烟：
   ```bash
   curl -s localhost:8000/api/healthz
   docker logs --tail 50 luyun        # 看 entrypoint 是否报了 playwright/venv 问题
   ```

**不需要重建镜像**——系统包（`sqlite3` 等）与本次改动无关，只换了 app 树。

### 2.2 systemd 部署

同上，后台操作。区别是重启走 `systemctl`，日志看 `journalctl -u luyun -f`。

### 2.3 升级后确认

- [ ] `/api/healthz` 返回 `version: 0.6.0`
- [ ] 后台订单列表有数据、能翻页
- [ ] KDS 厨房屏正常刷新
- [ ] admin 数据浏览器能编辑一行
- [ ] 用原密码能登录

---

## 3. 场景 B：升级 + 切 PostgreSQL

### ⚠️ 三个必须先知道的事实

1. **Docker 形态必须先重建镜像**。0.6.0 的 `Dockerfile` 才装了 `postgresql-client`
   （`pg_dump` / `psql`），而「系统更新」只替换 app 树、**不重建镜像**。不重建的话，
   切到 PG 后下次更新的 `backing_up` 阶段会因找不到 `pg_dump` 失败，把后续更新全部堵死。
   `enable_postgres.sh` 已把重建作为第一步。
2. **Admin 的「备份导出/导入」在 0.6.0 当时的 PG 后端不支持**（会明确报错）；
   v0.6.11 起改为把业务数据打包成 `app.pgdump` 整库快照（恢复即整库覆盖，且不能与
   SQLite 的备份包互灌）。更新前强制备份与定时冷备从一开始就支持（产出
   `app.pgdump`，见第 6 节）。
3. **回滚有取舍**：切回 `sqlite` 能回到旧库，但切到 PG 之后新增的数据**不会**回到 SQLite。

### 3.1 Docker：一键切换

```bash
# 前置：升级到 0.6.0 并确认应用正常（场景 A）
cd /opt/luyun        # 或你的部署目录

# PG 密码（compose 的 pg profile 必填）
export POSTGRES_PASSWORD='<强密码>'

sudo -E bash deploy/enable_postgres.sh --dry-run   # 先预览
sudo -E bash deploy/enable_postgres.sh             # 执行
```

脚本按下面顺序自动完成（幂等，可重复跑）：

| 步骤 | 动作 |
|---|---|
| 1 | **重建镜像**（拿到 `pg_dump` / `psql`）|
| 2 | 起 `postgres` 服务（compose `pg` profile）|
| 3 | 建库 + 建用户 |
| 4 | 应用 `migrations/pg/0001_initial_schema.sql` |
| 5 | 停容器 → 备份 SQLite → 迁移数据 → 重置 identity 序列 |
| 6 | 写 `deploy/env.production`：`DATABASE_BACKEND=postgres` + `POSTGRES_DSN` |
| 7 | 启动容器 → 冒烟（healthz + orders 行数比对）|

> `-E` 是必须的：脚本要读 `POSTGRES_PASSWORD`。

### 3.2 systemd：一键切换

```bash
cd /opt/luyun
sudo bash deploy/enable_postgres.sh --dry-run
sudo bash deploy/enable_postgres.sh
```

区别：PG 装在本机（`apt`/`dnf`/`yum` 自动识别），密码由脚本用 `openssl` 生成；
不需要 `POSTGRES_PASSWORD`，也不需要重建镜像。

### 3.3 切完后的冒烟清单

- [ ] `curl -s localhost:8000/api/healthz` → `database` 为健康
- [ ] 后台订单列表有数据（与切换前条数一致）
- [ ] KDS 能正常刷新与出餐
- [ ] admin 数据浏览器能翻页、编辑一行
- [ ] 用原密码登录成功
- [ ] 卫生模块数据在（员工、区域、检查项）
- [ ] 手动触发一次更新预检，`database` 灯是绿的

---

## 4. 手工步骤（脚本不适用时）

脚本覆盖不到的场合（离线、外部数据库、非标准路径）：

```bash
# 1) 建库建用户（PG 已就绪）
psql -U postgres -c "CREATE USER luyun WITH PASSWORD '<pw>';"
psql -U postgres -c "CREATE DATABASE luyun OWNER luyun;"

# 2) 应用 schema（建表；应用本身不会建表）
psql "postgresql://luyun:<pw>@127.0.0.1:5432/luyun" \
     -v ON_ERROR_STOP=1 -f migrations/pg/0001_initial_schema.sql

# 3) 停机窗口内搬数据（源库只读，回滚无损）
systemctl stop luyun          # 或 docker stop luyun
.venv/bin/python scripts/archive/migrate_sqlite_to_postgres.py --dry-run
.venv/bin/python scripts/archive/migrate_sqlite_to_postgres.py \
    --dsn "postgresql://luyun:<pw>@127.0.0.1:5432/luyun" --apply

# 4) 写 deploy/env.production：DATABASE_BACKEND=postgres + POSTGRES_DSN
# 5) 启动
systemctl start luyun         # 或 docker start luyun
```

**迁移脚本会做**：按外键拓扑排序插入、`tenant_id` 走默认值 1、每表
`TRUNCATE + COPY`（可重复执行）、结束后重置所有 identity 序列。

**注意**：迁移脚本不做增量同步——必须在停机窗口内执行。

---

## 5. 回滚

### 只升级过（仍是 SQLite）

装回更旧的发行包：「系统更新」里选旧 tag 即可（Admin 支持含更旧 tag）。

### 已切 PG

```bash
# 1) 把 deploy/env.production 改回
DATABASE_BACKEND=sqlite

# 2) 重启
systemctl restart luyun       # 或 docker restart luyun
```

`data/app.db` 全程只读、原样保留，所以**回滚不丢数据**。但切到 PG 之后产生的新数据
不在 SQLite 里——需要人工取舍。

观察期结束前**不要删** `data/app.db`。

---

## 6. PG 下的备份与恢复

| 能力 | 状态 |
|---|---|
| 更新前强制备份 | ✅ 产出 `app.pgdump`（`pg_dump --format=custom`）|
| 定时冷备 | ✅ 归档内含 `app.pgdump` |
| Admin 备份导出/导入 | ✅ v0.6.11 起业务数据为 `app.pgdump` 整库快照（恢复只能整库覆盖）；0.6.0 当时会报错，那时走手工 |

**手工备份**：

```bash
pg_dump --format=custom --no-owner --no-acl \
  -d "postgresql://luyun:<pw>@127.0.0.1:5432/luyun" \
  -f /tmp/app-$(date +%Y%m%d_%H%M%S).pgdump
```

**手工恢复**：

```bash
systemctl stop luyun          # 或 docker stop luyun
pg_restore --clean --if-exists --no-owner --no-acl \
  -d "postgresql://luyun:<pw>@127.0.0.1:5432/luyun" \
  data/restore_snapshots/<ts>/app.pgdump
systemctl start luyun
```

恢复后若报主键冲突，说明 identity 序列没跟上——按 `migrations/pg/README.md` 的
`setval` 段落重置。

---

## 7. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 更新卡在 `backing_up` | Docker 镜像里没有 `pg_dump` | `docker compose -f deploy/docker-compose.yml build` 重建镜像 |
| 预检 `database` 红灯 | PG 不可达 / `POSTGRES_DSN` 写错 | 检查 PG 服务与 DSN；Docker 下 host 用 compose 服务名 `postgres` |
| 切 PG 后启动报连接失败 | 容器内 DNS 或密码错 | `docker exec luyun env \| grep POSTGRES`；确认 `pg_hba.conf` 允许该网络 |
| 迁移报主键冲突 | 序列未推进 | 跑 `migrations/pg/README.md` 的 `setval` 段 |
| `syncing_deps` 耗时很长（2C4G）| 装机 + chromium 安装 | 正常，属一次性成本；`ensure_playwright_browsers` 失败不阻断启动 |
| Admin 导出备份报 400 | 0.6.0 的 PG 后端不导出业务数据 | 属预期（v0.6.11 起改为打包整库快照 `app.pgdump`）。旧版本可取消勾选「业务数据」，业务数据用第 6 节的手工命令 |

---

## 8. 已知限制

1. **PG 后端仍是单 worker**。Redis 容器已备（compose `pg` profile）；0.7.0 起 realtime
   nudge 已经它跨进程广播（`REDIS_URL`，见 `deploy/README.md` 10.1.1），但日志缓冲 /
   爬虫计数器仍在进程内存里，7 个常驻后台循环也还没有分布式选主。
2. **Admin 备份导出/导入在 PG 下的业务数据是整库快照**。0.6.0 当时整块报错，之后先
   收敛为「导出照常可用、业务数据置灰」，v0.6.11 起改为打包 `app.pgdump`
   整库快照（恢复即整库覆盖，不支持合并导入）。更新前备份与定时冷备从一开始就支持。
3. **时间戳仍是 TEXT、金额仍是浮点**。切换到 `TIMESTAMPTZ` / `NUMERIC` 是独立议题。
4. **迁移脚本不做增量同步**，必须在停机窗口内执行。
