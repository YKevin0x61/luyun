# 单机部署指南

> **发版与部署操作规范（推荐先读）**：[`docs/RELEASE_AND_DEPLOY.md`](../docs/RELEASE_AND_DEPLOY.md)  
> 本文侧重组件安装细节（反代、备份 timer、手工对照）；升级心智与发版清单以那份为准。

本系统已定为**单机、单实例、单 uvicorn worker**部署：没有以容器镜像 pull 为
真相的编排（Docker 只作进程外壳）。数据后端**只有 PostgreSQL**（ADR 0089）：
业务表、`sop_*`、`hygiene_*` 与日志表 `logs` 同在**一个库**里，`DATABASE_BACKEND`
默认 `postgres`，值不是 `postgres` 时应用启动即失败并打印迁移指引。结构由
`migrations/pg/*.sql` 建立，新装与从遗留 SQLite 迁移见第 10 节。
`main:app` 这一个 FastAPI 进程同时提供：

- REST API（`/api/*`）
- WebSocket 实时推送（`/ws/realtime`）
- 管理后台 SPA（`admin-web/dist`，由反代或 FastAPI 托管）
- KDS 看板构建产物（`public/kds/`，挂载在 `/kds`）

生产交付与升级遵循 **ADR 0011**（详见 `docs/adr/0011-release-bundle-update.md`、
`CONTEXT.md`「部署与更新」；[ADR 0010](../docs/adr/0010-github-release-update-job.md) 已取代）：

| 概念 | 含义 |
|---|---|
| **发行版 (Release)** | 正式 GitHub Release + **发行包**等附件 |
| **发行包 (Release Bundle)** | `luyun-release-bundle.tar.gz`（+ `SHA256SUMS`）：应用树 + 预构建 Admin/KDS + 版本清单 |
| **版本清单 (Release Manifest)** | 本机已装发行版身份；版本检测以此为准（不是 git tag） |
| **运行实例 (Runtime Instance)** | 店内这台正在跑的单机部署 |
| **版本检测 (Version Check)** | 对比本机版本清单与远端正式 Release，并给出**更新环境自检** |
| **更新环境自检 (Update Preflight)** | 下载/校验/切换/重启条件的红绿灯；不满足则禁止应用更新 |
| **应用更新 (Apply Update)** | 管理后台选定目标 Release 后发起更新（回滚=再装更旧发行包） |
| **更新作业 (Update Job)** | systemd oneshot 或 Docker 旁路进程：备份 → 下发行包 → 原子切换 → 条件 pip → 重启 |
| **引导安装 (Bootstrap Install)** | 新机器用同款发行包装到「应用进程可启动」（公开仓匿名下载，无需 PAT） |

运行实例**不跟随 branch tip**，也**不在生产机上跑** `npm` / `uni` /
`scripts/build_kds.sh`。前端产物只在发版时构建，由 Update Job / Bootstrap
从 **发行包**安装（布局契约：`docs/release-asset-layout.md`）。旧的分拆
`admin-web-dist.tar.gz` / `kds-dist.tar.gz` + git checkout **已退役**。

生产环境用 Caddy（首选）或 Nginx 做反向代理 + TLS 终结，把 `/api/*`、
`/ws/*` 转发给后端进程，把管理后台 SPA（`admin-web/dist`）交给反代层直接
托管。**为什么必须单 worker**：实时推送 hub（`services/realtime/hub.py`）、
内存日志缓冲区、爬虫失败计数等状态都保存在单个 Python 进程内存里，多
worker/多进程会导致状态分裂、WebSocket 订阅收不到推送、甚至重复采集订单。

本目录（`deploy/`）内容：

| 文件 | 用途 |
|---|---|
| `luyun.service` | systemd 单元，常驻运行 `uvicorn main:app`（单 worker） |
| `luyun-update.service` | systemd oneshot，Admin「系统更新」里 Apply Update 触发的 Update Job |
| `Dockerfile` / `docker-compose.yml` / `docker-entrypoint.sh` | Docker / 1Panel 进程外壳（绑定挂载发行包树；升级仍走发行包） |
| `.env.docker.example` | Compose 宿主机变量模板（复制为 `.env.docker`） |
| `Caddyfile` | Caddy 反向代理配置（首选，自动 HTTPS） |
| `nginx.conf` | Nginx 反向代理配置示例（与 Caddyfile 等价） |
| `env.production.example` | 生产环境变量示例（含 GitHub Release 说明；PAT 可选） |
| `enable_postgres.sh` | 一键切到 PostgreSQL 后端（装库 → 建库建用户 → 应用 schema → 迁移数据 → 写 env → 重启 → 冒烟；幂等，`--dry-run` 可预览） |
| `backup.sh` | 冷备脚本（`pg_dump` → `app.pgdump` 整库快照），含凭据文件/密钥 + 保留策略 |
| `luyun-backup.service` / `luyun-backup.timer` | systemd timer，每日调用 `backup.sh` |
| `README.md` | 本文档 |

相关脚本（仓库 `scripts/`，不在本目录）：

| 脚本 | 用途 |
|---|---|
| `scripts/bootstrap_install.sh` | 新机器 Bootstrap Install（下载/校验发行包） |
| `scripts/docker_up.sh` | Docker Compose 一键拉起（进程外壳） |
| `scripts/run_update_job.py` | Update Job 执行体（由 `luyun-update.service` 或 Docker 旁路调用） |
| `scripts/publish_release.sh` | 开发者发布 GitHub Release（产出发行包） |

Admin 与 KDS 构建产物包含各自的 Service Worker 和 Web App Manifest。反代需要
让 `/sw.js`、`/pwa/*` 与 `/kds/sw.js`、`/kds/manifest.webmanifest` 到达
FastAPI；FastAPI 会为 Service Worker/manifest/HTML 设置 `no-cache`，并为
哈希静态资源设置 immutable 缓存。Service Worker 不会缓存 `/api/*`、`/ws/*`、
上传下载或卫生图片。

---

## 1. 新机器：Bootstrap Install（推荐入口）

新机器用 Bootstrap 装到「应用进程可启动」，**不要求安装 Node**，**不需要
Deploy Key / git clone / Releases PAT**（仓库已公开）。  
**一键命令（推荐）**见 [`docs/RELEASE_AND_DEPLOY.md`](../docs/RELEASE_AND_DEPLOY.md) §4.2：

```bash
curl -fsSL \
  -L https://github.com/YKevin0x61/luyun/releases/latest/download/install.sh \
  | sudo bash
```

默认装**最新正式 Release**。详情见 `docs/RELEASE_AND_DEPLOY.md` §4.2。

脚本会完成：

1. 匿名下载并硬校验 `luyun-release-bundle.tar.gz` + `SHA256SUMS`
2. 解压发行包到 `<deploy-dir>`（含预构建 Admin/KDS 与 `RELEASE_MANIFEST.json`）
3. 写入 `<deploy-dir>/deploy/env.production`（含 `GITHUB_REPO`；
   `GITHUB_RELEASES_TOKEN` 可为空，mode 600；供 Version Check / Update Job 复用）
4. 创建 `.venv`，`pip install -r requirements.txt`
5. 安装 Playwright Chromium（生产 Linux 带 `--with-deps`）
6. 安装并 **enable**（不 start）`luyun.service` + `luyun-update.service`
   （非 root 时打印需 sudo 的单元安装步骤）

### Bootstrap 明确不做（人工后续）

脚本结束会打印剩余步骤；这些**不在** Bootstrap 范围内：

- 编辑 `deploy/env.production` 填齐 `LUYUN_CRED_KEY` 等非 GitHub 项
- 配置反向代理 + TLS（见第 3 节 Caddy/Nginx）
- 启动主服务后，在 Admin `/setup` 填写 POS 凭据
- DNS / 域名申请

> 日常冷备 timer（`luyun-backup.timer`）也不由 Bootstrap 自动启用；需要时按
> 第 4 节单独安装。

装完后建议：

```bash
# Bootstrap 只 enable 单元；确认 env 后由人工 start 主服务
sudo systemctl start luyun.service
systemctl status luyun
curl -s http://127.0.0.1:8000/api/system/status | head
```

### 1.1 Docker / Compose（进程外壳）

适合 1Panel / Compose：容器只提供 Python + Playwright 运行时；应用树仍来自
**发行包**（首次可由入口脚本自动下载，日常升级走 Admin「系统更新」）。

```bash
# 仓库根目录
cp deploy/.env.docker.example deploy/.env.docker   # 按需改端口/目录
./scripts/docker_up.sh
# 等价：
# docker compose -f deploy/docker-compose.yml --env-file deploy/.env.docker up -d --build
```

**必须挂载父目录，不要挂直播应用目录本身。** Update Job 会在同卷上
`rename app ↔ app.prev / app.next`；若把 `runtime/app` 直接当成 volume，原子切换会失败。

| 宿主机（默认） | 容器 |
|---|---|
| `deploy/runtime/`（父目录） | `/srv/luyun` |
| `deploy/runtime/app/`（直播树） | `/srv/luyun/app` |
| `/var/run/docker.sock` | `/var/run/docker.sock` |

环境要点：`LUYUN_DEPLOY_MODE=docker`、`LUYUN_DOCKER_CONTAINER` 与
`container_name` 一致、`RELEASE_UPDATE_REPO_DIR=/srv/luyun/app`。  
首次 `runtime/app` 为空时，入口默认拉取最新正式发行包（`LUYUN_DOCKER_AUTO_BOOTSTRAP=1`）。  
之后编辑 `runtime/app/deploy/env.production`（`LUYUN_CRED_KEY` 等），反代可指到
`127.0.0.1:${LUYUN_HOST_PORT}`。

---

## 2. 升级：Version Check → Update Preflight → Apply Update → Update Job →（PG）应用数据库迁移

店内日常升级**不要** SSH 上去 `git checkout` 再 `npm run build`。正确路径：

1. **发版**（开发者）：`scripts/publish_release.sh` 创建正式 GitHub Release
   并附带发行包（见第 7 节）。
2. **版本检测**（店员/管理员）：登录管理后台 → `/setup` →「系统更新」。
   只读展示本机已装发行版（**版本清单**）、远端正式 Release 列表、是否有更新，
   以及**更新环境自检**红绿灯。
3. **应用更新**：仅通过更新环境自检的运行实例可点；选定目标正式 Release（**含更旧 tag，即回滚**），
   确认目标；若落在营业高峰会提示，需显式覆盖后才能继续。Web 进程只写入作业意图并
   拉起旁路作业（systemd：`systemctl start luyun-update.service`；Docker：后台
   `scripts/run_update_job.py`），不在 uvicorn 进程内改代码。
4. **更新作业**阶段（进度见 `data/update_job.json`，日志
   `data/update_job.log` / `journalctl -u luyun-update`）：

| 阶段 | 含义 |
|---|---|
| `queued` | 已记录意图，oneshot / Docker 旁路作业待跑/刚启动 |
| `backing_up` | **强制备份**（失败则不改动线上应用树） |
| `fetching_bundle` | 下载发行包 + `SHA256SUMS` 并硬校验 |
| `installing` | 旁路解压后原子切换（保留上一版目录；不覆盖 `data/` / 凭据） |
| `syncing_deps` | 仅当 `requirements_fingerprint` 变化时 pip；随后幂等同步 Playwright 浏览器（失败只告警，不阻断更新） |
| `restarting` | 重启主服务（systemd 或 Docker socket） |
| `succeeded` / `failed` | 终态；失败且已离开旧树时切回上一版并尽量拉起主服务 |

并发：已有进行中的 Update Job 时，新的 Apply Update 会被拒绝。
`data/` 业务库与凭据在代码更新过程中保留；备份另见第 4 节。

5. **应用数据库迁移**：健康确认之后进「系统更新」→「数据库迁移」→「应用待执行迁移」。

   更新作业按设计**不碰数据库结构**（`_connect_postgres` 明确不在启动期改结构，结构变更
   要可追溯），所以 schema 变更得单独应用一次。版本检测已把待应用条数显示在版本状态卡上，
   不用靠记性；应用记录写在 `schema_migrations` 表，能看出当前到哪一版。详见
   `migrations/pg/README.md`。

> **数据库是 PostgreSQL**：上面 `backing_up` 阶段的强制备份走 `pg_dump` 产出
> `app.pgdump`（不再有 `app.db`）。首次部署、从遗留 SQLite 迁移、恢复与回滚见
> [第 10 节](#10-postgresql唯一后端)。

### 依赖与浏览器的版本一致性

Playwright 的 Python 包与浏览器 build 一一对应（如 lib 1.63.0 ↔
`chromium` / `chromium_headless_shell` build 1243）。两者分开升级就会出现
`BrowserType.launch: Executable doesn't exist at /ms-playwright/chromium_headless_shell-1243/...`。
因此：

- `requirements.txt` 钉死 `playwright==1.63.0`；升级该版本时必须同步浏览器；
- 更新作业在 `syncing_deps` 阶段用部署 venv 的解释器执行
  `.venv/bin/python -m playwright install chromium`（幂等，目标 build 已存在则秒退）；
- Docker 入口在启动 uvicorn 前用 `.venv` 的 playwright 真跑一次 `chromium.launch()`，
  失败则补装（只比对 `executable_path` 不够：headless 启动走的是 `chromium_headless_shell`）；
- 爬虫自身在 launch 报「Executable doesn't exist」时也会自动补装并重试一次，可用
  `SCRAPER_BROWSER_AUTO_INSTALL=0` 关闭。

### 别用一次性脚本碰生产库

`tests/conftest.py` 只保护 `pytest` 那条路径：它把 DSN 钉死到专用测试库
`luyun_test`。**任何绕过 `pytest` 的代码都不会被保护**——`python -c`、REPL、临时
验证脚本、没写隔离的 `scripts/` 运维脚本，都会按 `.env` 里的 `POSTGRES_DSN`
**直接连上生产库**，写进去的行没人会注意到。

要在本机做验证（环境变量必须**早于** `from config import settings`——pydantic-settings
的优先级是 env > `.env`；库不存在就先跑一次 `pytest`，conftest 会建）：

```bash
POSTGRES_DSN=postgresql://localhost:5432/luyun_test .venv/bin/python -c "..."
```

确实需要连生产库时只读、只 `SELECT`，并先打印 `settings.POSTGRES_DSN` 确认。

### 磁盘水位

- 「更新环境自检」包含磁盘余量：可用空间低于 `UPDATE_MIN_FREE_MB`（默认 2048MB）
  时禁止应用更新——满盘跑 pip sync 会把 `.venv` 写坏；
- 进程内磁盘守护每 `DISK_GUARD_INTERVAL_SECONDS`（默认 300s）检查一次，达到
  `DISK_WARN_PCT` / `DISK_CRITICAL_PCT`（默认 85% / 92%）时写日志并通过 realtime
  `admin` nudge 提示管理端；`DISK_GUARD_ENABLED=0` 可关闭；
- `GET /api/healthz`（免鉴权、只读，返回 DB 状态与聚合磁盘水位）可供 Docker
  `HEALTHCHECK` 或反向代理探针使用。磁盘水位高**不**改状态码：重启容器腾不出空间，
  只会变成重启循环。

**Docker / 1Panel：** 见下方「Docker 部署」；交付仍是发行包，不是镜像 pull。

### 旧版 git 店面迁移

仍在用 git checkout + 分拆前端 tar 的店面：**下一次成功的 Apply Update**
会切到版本清单身份，无长期双轨。详见 `docs/RELEASE_AND_DEPLOY.md` §5.5。

### 回滚

- **软件回滚（首选）**：在「系统更新」里对**更旧的正式 Release**再执行一次
  Apply Update（再下载该版发行包）。
- **数据回滚**：库数据用备份里的整库快照（`app.pgdump`）走 `pg_restore` 覆盖回
  PostgreSQL（见 §10.4；没有可覆盖回 `data/` 的 `.db` 文件了）；凭据文件
  `credentials.enc` / `.cred_key` 仍是从归档覆盖回 `data/`——先
  `sudo systemctl stop luyun`，再覆盖，避免与运行中进程写冲突。

### SSH 应急（非日常）

仅在 Admin 不可用等应急场景下，可对照 Update Job 逻辑手动排查；**仍不要**在
生产机跑 Admin/KDS 构建。日常升级以「系统更新」为准。

---

## 3. 反向代理配置

Bootstrap **不**配置反代；装完后按需执行。

### 3.1 Caddy（首选，自动申请/续期 Let's Encrypt 证书）

```bash
# 安装 Caddy（各发行版方式不同，参考 Caddy 官方文档）
sudo cp /opt/luyun/deploy/Caddyfile /etc/caddy/Caddyfile
# 编辑域名与 admin-web/dist 的绝对路径占位
sudo vim /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

### 3.2 Nginx（等价方案，见 `deploy/nginx.conf`）

```bash
sudo cp /opt/luyun/deploy/nginx.conf /etc/nginx/sites-available/luyun.conf
sudo ln -s /etc/nginx/sites-available/luyun.conf /etc/nginx/sites-enabled/luyun.conf
# 编辑域名、admin-web/dist 路径、TLS 证书路径占位
sudo vim /etc/nginx/sites-available/luyun.conf
sudo nginx -t && sudo systemctl reload nginx
# 建议用 certbot 自动签发/续期证书：sudo certbot --nginx -d your-domain.example.com
```

两份配置的路由逻辑一致（详细注释见各自文件头部）：

- `/api/*`、`/ws/*` → 反代到 `127.0.0.1:8000`（WebSocket 升级：Caddy 自动
  处理；Nginx 需要显式 `proxy_set_header Upgrade/Connection`，配置里已带）。
- 管理后台各页面（含 `/login`、`/setup`、`/`、`/admin`、`/sales-report`、
  `/logs`、`/prep-plan`、`/wecom-push`、`/recipe*`）→ 由 `admin-web/dist`
  提供，history 模式路由用 `try_files` 回退到 `index.html`，交给 vue-router
  接管。
- `/kds/*`、Swagger `/docs`、以及仍由后端 `StaticFiles` / `FileResponse`
  提供的路径 → 按各自配置转发或直出（见 Caddyfile / nginx.conf 注释）。

---

## 4. 备份配置

备份在管理后台 `/setup` → 「备份中心」统一呈现为一份**备份点**列表：本机回滚
快照、导出备份与宿主机冷备三种介质。页面备份与冷备走同一套创建逻辑
（`services/backup_service.py`），两条路径不会给出互相矛盾的结论。

Update Job 在切换应用树前会**强制**创建一份来由为「更新作业前」的本机回滚
快照（该快照与最近一份备份点永不自动清理）；日常仍建议装 timer 做定期冷备。

```bash
# 手动跑一次试试
cd /opt/luyun
./deploy/backup.sh
# 产出 backups/<timestamp>/luyun_cold_backup.tar 与 backups/cold_backup_status.json

# 用 systemd timer 每日自动跑（推荐）：
sudo cp deploy/luyun-backup.service /etc/systemd/system/luyun-backup.service
sudo cp deploy/luyun-backup.timer   /etc/systemd/system/luyun-backup.timer
# 编辑两个文件里的 /opt/luyun 占位路径
sudo systemctl daemon-reload
sudo systemctl enable --now luyun-backup.timer

# 查看下次触发时间 / 上次运行结果
systemctl list-timers luyun-backup.timer
journalctl -u luyun-backup
```

不用 systemd timer 的话，`deploy/backup.sh` 内也附了等价的 crontab 示例
（文件末尾注释）。脚本只是调度入口，应用侧逻辑在 `scripts/cold_backup.py`。

冷备产物是**一份可校验的单一归档**：库快照 + `credentials.enc` +
`.cred_key` + 两类卫生照片（标准图含全部历史版本、其它照片）+ `manifest.json`
清单 + `SHA256SUMS` 校验和。库快照由 `pg_dump --format=custom` 产出整库快照（成员
`app.pgdump`）——`data/app.db` 不再是冷备的前置条件，也不再有任何 SQLite 分支
（ADR 0089）。冷备**不需要停服务**，保留份数从 PostgreSQL 的 `app_settings` 读取
（`services/backup_retention.load_from_pg_sync`）。
每次运行还会在固定位置写一份 `backups/cold_backup_status.json`（时间、结果、
归档名、体积、校验结论、错误信息），备份中心据此显示最近一次冷备结论；状态文件
缺失时（旧部署）退回扫描 `backups/*/` 目录兜底。

归档内含 `.cred_key`（凭据加密密钥），请确保 `backups/` 权限受控。保留份数由
运行配置决定（管理后台「备份中心 → 保留与清理」可改：本机回滚快照默认 5、上限 20；
导出备份本机副本默认 5、上限 20；冷备默认 14、上限 90），也可用
`./deploy/backup.sh --retention 30` 临时覆盖冷备份数；最近一份冷备永不自动删。
任一步失败时退出码非零、状态文件记录失败原因，且不留下会被误认为有效备份的
半成品。恢复时优先用管理后台 `/setup` →「备份中心」→ 恢复；手工还原把归档内的
`app.pgdump` 走 `pg_restore`、凭据文件放回 `data/`，见 §10.4。

> 建议再把 `backups/` 目录定期同步到异地存储（对象存储、另一台机器等），
> 单机备份只能防误删/误改，防不了硬盘/整机故障。这部分本项目暂未提供
> 现成脚本，需要按你实际使用的存储服务自行补充（比如在 `backup.sh` 跑完后
> 加一行 `rsync`/`rclone` 命令）。

> 冷备依赖 `pg_dump`（`postgresql-client`，版本需 ≥ 服务端）。`PYTHON_BIN` 可覆盖
> 执行用的解释器。

> **不在备份范围内**（页面会明确列出，清单见 `services/backup_points.NOT_BACKED_UP`）：
> 日志（`logs` 表；与业务表同库，不再有独立的 `logs.db` 文件）、采集状态文件、
> 更新访问凭据 `github_release.enc`、更新作业状态与更新历史。

---

## 5. 首次 `/login` 与 `/setup` 初始化

Bootstrap **不**填写 POS 凭据；反代就绪后：

1. 应用鉴权（管理员账号密码）：首次访问 `GET /login`（经反代域名，或直连
   `http://127.0.0.1:8000/login` 排查），会调用 `POST /api/auth/init` 完成
   初始化（`api/auth.py`）。初始化前，写接口是否对本机开放取决于
   `ALLOW_UNAUTH_SETUP_FROM_LOCALHOST`——**生产环境必须是 `false`**（已在
   `env.production.example` 里默认给出并解释原因）。
2. POS 系统登录凭据（账号/密码/门店 ID）：访问 `/setup` 页面填写，加密保存
   在 `data/credentials.enc`（加密密钥见 `LUYUN_CRED_KEY`，说明见
   `env.production.example`）。
3. 之后同一页「系统更新」可用于版本检测 / 更新环境自检 / 应用更新（公开仓
   无需 PAT；Token 仅在 API 限流时可选）。

两者都建议在部署机器上通过 SSH 隧道或临时开放安全组，只对你自己的 IP
可访问的情况下完成，避免初始化窗口暴露在公网。

---

## 6. 日志查看

- 应用进程日志（含 uvicorn 访问日志、爬虫日志等）：
  ```bash
  journalctl -u luyun -f          # 实时跟踪
  journalctl -u luyun --since "1 hour ago"
  ```
- Update Job：`journalctl -u luyun-update`，以及 `data/update_job.log` /
  `data/update_job.json`
- 备份任务日志：`journalctl -u luyun-backup`
- 应用内近期日志（`logs` 表，与业务表同库；带级别/logger 过滤）：管理后台
  「实时日志」页面（`/logs`，需要登录），或 `GET /api/logs/*` 系列接口。
  - 写入走 `queue.Queue` 批量落库（`services/log_storage.py`，纯 PG 实现）；
    启动与运行期每 `LOG_MAINTENANCE_INTERVAL_SECONDS`（默认 6h）按
    `LOG_RETENTION_DAYS` 清理过期日志（空间回收交给 PG autovacuum）；
  - SQLite 时代的 WAL / `synchronous=NORMAL` / 启动 `quick_check` / 损坏隔离
    `logs.db.corrupt.<时间戳>` / `.forensics.txt` 已随 SQLite 一起退役（ADR 0089），
    `data/logs.db` 不再创建；
  - **磁盘满不会被当成损坏**：那类写入失败只丢弃当批日志并计入 `queue_dropped`
    （`GET /api/logs/stats`），不会清空历史。
- Caddy/Nginx 访问日志：各自默认日志位置（`/var/log/caddy/`、
  `/var/log/nginx/`），或按需在 Caddyfile/nginx.conf 里加 `log` 配置。

---

## 7. 开发者：发布 Release（发行包，生产机无 Node）

店内升级依赖正式 GitHub Release。开发者在干净工作区、且 `config.py` 的
`APP_VERSION` 与 tag（去掉可选前导 `v`）对齐后：

```bash
# 校验契约（不构建、不打 tag、不调 gh）
./scripts/publish_release.sh --dry-run v0.1.0

# 构建 Admin + KDS、打发行包、打 tag、gh release create 并上传资产
./scripts/publish_release.sh v0.1.0
```

资产布局（Update Job / Bootstrap 契约）见 `docs/release-asset-layout.md`：

| Release 资产 | 角色 |
|---|---|
| `luyun-release-bundle.tar.gz` | 发行包（应用树 + Admin/KDS + 版本清单） |
| `SHA256SUMS` | 硬校验 sidecar |
| `install.sh` | curl\|bash 引导入口 |

**只有发版机 / CI 需要 Node**；Runtime Instance 只安装上述发行包。  
分拆的 `admin-web-dist.tar.gz` / `kds-dist.tar.gz` 已不再是店内契约。

---

## 8. 手工对照（非 Bootstrap 路径）

一般新机器请用第 1 节。若你已用别的方式拿到应用树、需要对照组件，可参考：

```bash
cd /opt/luyun
# 优先：从正式 Release 下载发行包并按 SHA256SUMS 校验后解压（见 release-asset-layout.md）
# 不要在生产机 npm/uni 构建；不要依赖 git clone 作为交付

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium

cp deploy/env.production.example deploy/env.production
# 填 LUYUN_CRED_KEY 等；GITHUB_RELEASES_TOKEN 公开仓可留空；chmod 600
# 确认 RELEASE_MANIFEST.json 已在部署根目录

sudo cp deploy/luyun.service /etc/systemd/system/luyun.service
sudo cp deploy/luyun-update.service /etc/systemd/system/luyun-update.service
# 两文件内 /opt/luyun 占位换成实际路径
sudo systemctl daemon-reload
sudo systemctl enable --now luyun.service
sudo systemctl enable luyun-update.service   # oneshot，按需 start
```

`luyun.service` 的 `ExecStart` 指定 `--host 127.0.0.1 --port 8000 --workers 1`，
只监听本机回环；对外访问全部经由反向代理。

---

## 9. 关键约束回顾

- **单实例、单 worker**：不要给 `uvicorn`/`gunicorn` 配置多进程。库在 PostgreSQL
  服务端，多机可连——SQLite 文件锁那条约束随 SQLite 退场（ADR 0089）——但
  **单 worker 仍然成立**：realtime hub、日志缓冲、爬虫计数器还在进程内存里，
  除非先把它们外置到 Redis（尚未接入）。
- **Docker 仅可作进程外壳**：见「Docker 部署」；**不要**把 `docker pull`
  镜像当作本产品的店内交付真相。
  裸机/VM 上用 systemd + venv 仍是默认路径。
- **生产机无 Node / 无运行时前端构建**：日常升级走发行包 + Update Job；
  发版用 `scripts/publish_release.sh`（见第 7 节与 ADR 0011）。
- **`WorkingDirectory` 必须是应用根目录**：`data/`、`backups/` 与各状态文件都按
  进程 cwd 解析相对路径。配方表 `sop_*` 与业务表同库（不再有独立的
  `data/recipes.db`），`RecipeStore` 的连接由 `main.py` 注入，已不自建 SQLite 文件。
- CORS（`main.py` 硬编码 `allow_origins=["*"]`）和爬虫营业时间
  （`scraper/pos_session.py` 硬编码 07:30–21:30）目前都不支持环境变量覆盖，
  属于代码常量，改动需要改代码而不是这份部署配置。

---

## 10. PostgreSQL（唯一后端）

PostgreSQL 是唯一后端（ADR 0089）：`DATABASE_BACKEND` 默认 `postgres`，值不是
`postgres` 时应用启动即失败并打印迁移指引，结构只能在 `migrations/pg/*.sql` 里改。
新机器先有一个可连的 PostgreSQL 并应用 `0001_initial_schema.sql`；从遗留 SQLite
门店迁移见 10.2 / 10.3。决策背景见
[ADR 0084](../docs/adr/0084-multi-store-reintroduce-postgres-redis.md)（引入 PG）与
[ADR 0089](../docs/adr/0089-retire-sqlite-postgres-only.md)（SQLite 退场）。

### 10.1 能力现状

| 能力 | 现状 |
|---|---|
| 读写 / KDS / admin 表格编辑 | ✓ |
| 更新前强制备份（`backing_up`） | ✓ `pg_dump` → `app.pgdump` |
| 定时冷备（`deploy/backup.sh`） | ✓ 同一条 `pg_dump` 路径 |
| Admin「备份导出 / 导入」 | 业务数据是 `app.pgdump` 整库快照；导入走 `pg_restore`，**只能整库覆盖**（无合并） |
| 必须单 worker | **是**——realtime hub / 日志缓冲 / 爬虫计数器还在进程内 |

> **冷备没有前置条件**：`scripts/cold_backup.py` 不再要求 `data/app.db` 存在，保留
> 份数也从 PostgreSQL 的 `app_settings` 读取，纯 PG 新装机器可直接跑
> `deploy/backup.sh`。
>
> `api/admin.py` 里的 `.db` 导出 / 导入与表结构管理目前仍在（依赖
> `db_core/backend/sqlite_export.py`），是待退役的残留路径。

Redis 容器仍在 `docker-compose.yml` 的 `pg` profile 里（默认不起），**代码尚未接入**，
先起它不改变任何行为。

### 10.2 从遗留 SQLite 迁移（推荐）

> **0.5.19 → 0.6.0 的完整升级方案**（含 Docker 形态、切 PG 前置、回滚、排查表）
> 见 [UPGRADE_TO_0_6_0.md](../docs/UPGRADE_TO_0_6_0.md)。该文是历史方案：0.6.x
> 之后 SQLite 已退场，文中的「继续用 SQLite」场景不再适用，只保留迁移路径供参考。

```bash
sudo bash deploy/enable_postgres.sh --dry-run   # 先预览它要做什么
sudo bash deploy/enable_postgres.sh             # 实际执行
```

这一条命令会自动完成：装 PostgreSQL（apt/dnf/yum 自动识别）→ 建库建用户（密码
自动生成）→ 应用 `0001` bootstrap schema → 停应用 → 备份 SQLite → 迁移数据 →
重置序列 → 写 `env.production` → 启动 → 冒烟检查（healthz + orders 行数比对）。
`0002` 起的增量脚本不在这一步应用——切完之后在后台 `/setup` →「系统更新」→
「数据库迁移」里应用。

脚本**幂等**，可重复执行；已完成的步骤会跳过。任何一步失败即中止，且
`data/app.db` 全程只读，所以失败后直接重跑；已切库之后的数据回滚见 10.4 / 10.5。

> 为什么要单独一条 `sudo` 命令，而不是在管理后台点一下？因为装系统包、以
> postgres 身份建库、改 systemd 服务都需要 root，而更新作业
> （`luyun-update.service`）刻意以 `luyun` 用户运行——那是安全设计，不应该为了
> 方便把 root 权限交给 Web 触发的作业。所以切换做成一次显式提权。

### 10.3 手工步骤（脚本不适用时）

脚本覆盖不到的场景（离线环境、已有的外部数据库、非 systemd 部署）按下面手工走。

```bash
# 1) 装 PostgreSQL 16 + client（pg_dump/pg_isready 是备份与预检依赖）
sudo apt-get install -y postgresql-16 postgresql-client-16

# 2) 建库建用户
sudo -u postgres psql -c "CREATE USER luyun WITH PASSWORD '<强密码>';"
sudo -u postgres psql -c "CREATE DATABASE luyun OWNER luyun;"

# 3) 应用 schema（这一步建表；应用本身不会建表）
psql "postgresql://luyun:<强密码>@127.0.0.1:5432/luyun" \
     -v ON_ERROR_STOP=1 -f migrations/pg/0001_initial_schema.sql

# 4) 停机窗口内搬数据（脚本对源库只读，回滚无损）
sudo systemctl stop luyun
sqlite3 data/app.db ".backup 'backups/pre-pg-migration.db'"
.venv/bin/python scripts/archive/migrate_sqlite_to_postgres.py --dry-run
.venv/bin/python scripts/archive/migrate_sqlite_to_postgres.py --dsn "postgresql://luyun:<强密码>@127.0.0.1:5432/luyun" --apply

# 5) 配置环境变量（deploy/env.production）
#    DATABASE_BACKEND=postgres
#    POSTGRES_DSN=postgresql://luyun:<强密码>@127.0.0.1:5432/luyun

# 6) 启动并冒烟：/api/healthz → 后台订单列表 → KDS → admin 表格编辑 → 原密码登录
sudo systemctl start luyun
curl -s localhost:8000/api/healthz
```

`deploy/luyun.service` 已声明 `After=postgresql.service` + `Wants=postgresql.service`
（弱依赖而非 `Requires`：写成强依赖会让没有这个 unit 的机器直接起不来；库在本机
自建还是外部实例都可以）。

Docker 形态下 `postgres` 服务随 compose 一起起（已去掉 profile），Redis 仍在 `pg`
profile 里，需要时显式带上（无需 root、无需改 env 之外的系统状态）：

```bash
docker compose -f deploy/docker-compose.yml --profile pg up -d
```

完整前置条件、冒烟清单与排错见
[`migrations/pg/README.md`](../migrations/pg/README.md)。

### 10.3.1 已有门店的升级

**仍跑在遗留 SQLite 上**：更新预检对非 `postgres` 后端判红（ADR 0089），所以先按
10.2 或 10.3 把数据迁到 PostgreSQL，再走管理后台「系统更新」。迁移脚本对源库只读，
`data/app.db` 原样保留；0.5.19 → 0.6.0 的历史方案见
[UPGRADE_TO_0_6_0.md](../docs/UPGRADE_TO_0_6_0.md)。

**已经跑在 PostgreSQL 上**：零额外步骤——管理后台 →「系统更新」→ 版本检测 → 应用
更新。升级作业会自动 `pg_dump` 备份、原子切换代码、保留 `data/`；`syncing_deps`
阶段会装上新声明的依赖。

### 10.4 备份与恢复

**页面内恢复（推荐，本机回滚快照）**：管理后台 `/setup` →「备份中心」→「备份点 →
本机回滚快照」，点「恢复整库数据」。这条路径会：

1. 先自动生成一份前置快照（同样的 `app.pgdump`，恢复失败可回退）；
2. 断开应用自己的数据库连接，再跑 `pg_restore --clean --if-exists --no-owner --no-acl`；
3. 重建连接并重置所有 identity 序列（避免恢复后新写入撞主键）；
4. 让当前后台会话失效——`auth` 表被一起替换，需要重新登录。

恢复期间服务短暂无法写库（采集会中断一轮），**不要在营业高峰做**。

**手工冷备（`pg_dump`）**：管理后台的「导出备份」（`.luyunbak`）会把业务数据打包成
整库快照（`app.pgdump`），恢复时按成员名走 `pg_restore --clean` 整库覆盖。
同样的命令也可以手工跑（与更新作业的 `backing_up` 阶段、`deploy/backup.sh` 是同一条）：

```bash
mkdir -p backups/manual
pg_dump --format=custom --no-owner --no-acl \
  -f "backups/manual/app-$(date +%Y%m%d_%H%M%S).pgdump" \
  "postgresql://luyun:<密码>@127.0.0.1:5432/luyun"
```

> `.luyunbak` 里同时带着凭据、运行配置、配方数据与两类卫生照片；业务数据
> 是整库快照，所以「业务数据」这一项的恢复粒度是**整库覆盖**，不是逐表合并——
> 导入面板会据此只提供「覆盖恢复」。

**手工恢复（冷备归档、或页面不可用时）**：

```bash
# 快照位置：data/restore_snapshots/<ts>/app.pgdump（本机回滚快照）
#           backups/<ts>/luyun_cold_backup.tar 内的 app.pgdump（冷备）
sudo systemctl stop luyun
pg_restore --clean --if-exists --no-owner --no-acl \
  -d "postgresql://luyun:<密码>@127.0.0.1:5432/luyun" data/restore_snapshots/<ts>/app.pgdump
sudo systemctl start luyun
```

手工路径恢复后如遇主键冲突，说明序列没跟上——按
[`migrations/pg/README.md`](../migrations/pg/README.md) 的 setval 段落重置
（页面内恢复会自动做这一步）。

### 10.5 回滚

- **软件回滚**：在「系统更新」里对更旧的正式 Release 再执行一次 Apply Update，
  照旧装回更旧发行包。
- **数据回滚**：只有 PG 快照一条路——本机回滚快照 / `.luyunbak` / 冷备归档里的
  `app.pgdump` 走 `pg_restore`（见 10.4）。**没有「把 `DATABASE_BACKEND` 改回
  `sqlite`」这一招**：SQLite 已退场（ADR 0089），那样改只会让应用启动直接失败。
  `data/app.db` / `data/logs.db` 是历史遗留文件：`app.db` 只剩迁移脚本
  （`scripts/archive/migrate_sqlite_to_postgres.py`）与待退役的备份/导出路径会读。

### 10.6 后台重置数据库密码

后台 `/setup` →「数据库凭据」可以查看当前连接信息并一键重置 PostgreSQL 业务角色的
密码，不需要登录服务器。

它依次做四件事：`ALTER USER`（用当前 DSN，只改自己这个角色）→ 用新密码另建连接
验证 → 把新 DSN 原子写回 `deploy/env.production` → 重启应用让新 DSN 生效。
**任何一步失败都会把密码改回原值**，不会留下「文件写了但库没改」这种下次重启就
连不上库的状态。

| 场景 | 行为 |
| --- | --- |
| 后端不是 `postgres`（代码里仍有这个分支，实际到不了：应用启动就会失败） | 面板只读展示，重置被拒：「当前后端不是 PostgreSQL，无法重置数据库密码」 |
| 连接串里没有密码（本机 trust 认证） | 拒绝重置（无法回滚），面板给出说明 |
| 设了 `LUYUN_POSTGRES_DSN` 环境变量 | 拒绝并提示：它优先于 `deploy/env.production`，写文件不生效 |
| 当前密码短于 16 位 | 面板警示，建议重置为 32 位随机密码 |

**安全约定**：与其它管理接口同一鉴权；重置需**二次输入后台管理员密码**；新密码由
服务端用 `secrets` 生成 32 位 DSN 安全字符（`A–Z a–z 0–9`），**响应里不回显**，
只落 `deploy/env.production`；审计日志只记「谁改了哪个角色」，不记密码。

> 密码在 `deploy/env.production` 里是明文（文件权限 600）。这个文件属于「更新时
> 保留」清单，升级不会覆盖它；Docker 形态下 entrypoint 启动时会 source 它并覆盖
> compose 注入的同名变量，所以改它就等于改了下一次启动用的 DSN。
>
> 前提：镜像里的 `pg_dump` 客户端版本必须 ≥ 服务端（否则更新前备份会失败），见
> `deploy/Dockerfile` 的注释与 `tests/test_docker_compose_contract.py` 的契约测试。
