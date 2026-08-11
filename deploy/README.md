# 单机部署指南

> **发版与部署操作规范（推荐先读）**：[`docs/RELEASE_AND_DEPLOY.md`](../docs/RELEASE_AND_DEPLOY.md)  
> 本文侧重组件安装细节（反代、备份 timer、手工对照）；升级心智与发版清单以那份为准。

本系统已定为**单机、单实例、单 uvicorn worker**部署：没有 Postgres、没有
Redis、没有以容器镜像 pull 为真相的编排。数据是 `data/` 目录下的一组 SQLite
文件（见 `config.py` 的 `DATABASE_PATHS`）。`main:app` 这一个 FastAPI 进程同时提供：

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
| `backup.sh` | SQLite 在线冷备脚本（`sqlite3 .backup`），含凭据文件/密钥 + 保留策略 |
| `luyun-backup.service` / `luyun-backup.timer` | systemd timer，每日调用 `backup.sh` |
| `README.md` | 本文档 |

相关脚本（仓库 `scripts/`，不在本目录）：

| 脚本 | 用途 |
|---|---|
| `scripts/bootstrap_install.sh` | 新机器 Bootstrap Install（下载/校验发行包） |
| `scripts/docker_up.sh` | Docker Compose 一键拉起（进程外壳） |
| `scripts/run_update_job.py` | Update Job 执行体（由 `luyun-update.service` 或 Docker 旁路调用） |
| `scripts/publish_release.sh` | 开发者发布 GitHub Release（产出发行包） |

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

## 2. 升级：Version Check → Update Preflight → Apply Update → Update Job

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
| `syncing_deps` | 仅当 `requirements_fingerprint` 变化时 pip；否则跳过 |
| `restarting` | 重启主服务（systemd 或 Docker socket） |
| `succeeded` / `failed` | 终态；失败且已离开旧树时切回上一版并尽量拉起主服务 |

并发：已有进行中的 Update Job 时，新的 Apply Update 会被拒绝。
`data/` 业务库与凭据在代码更新过程中保留；备份另见第 4 节。

**Docker / 1Panel：** 见下方「Docker 部署」；交付仍是发行包，不是镜像 pull。

### 旧版 git 店面迁移

仍在用 git checkout + 分拆前端 tar 的店面：**下一次成功的 Apply Update**
会切到版本清单身份，无长期双轨。详见 `docs/RELEASE_AND_DEPLOY.md` §5.5。

### 回滚

- **软件回滚（首选）**：在「系统更新」里对**更旧的正式 Release**再执行一次
  Apply Update（再下载该版发行包）。
- **数据回滚**：用第 4 节备份目录把 `.db` 与（若有）`credentials.enc` /
  `.cred_key` 覆盖回 `data/`——先 `sudo systemctl stop luyun`，再覆盖，避免
  与运行中进程写冲突。

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

Update Job 在切换应用树前会**强制**跑一轮与 `backup.sh` 同类的备份；日常仍建议
装 timer 做定期冷备。

```bash
# 手动跑一次试试
cd /opt/luyun
./deploy/backup.sh
ls backups/    # 每次运行生成一个时间戳目录，含 *.db 及（若存在）credentials.enc / .cred_key

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
（文件末尾注释）。

备份原理：对 `data/` 下每个 `*.db` 执行 `sqlite3 <db> ".backup <目标>"`——
这是 sqlite3 官方在线备份 API，即使数据库处于 WAL 模式且正被 `luyun.service`
写入，也能拿到一致性快照，**不需要停服务**。若已配置 POS 登录，脚本还会把
`data/credentials.enc` 与 `data/.cred_key` 复制进同一时间戳目录（备份含加密
密钥，请确保 `backups/` 权限受控）。恢复时需将 `.db` 与上述凭据文件一并还原
到 `data/`。默认保留最近 14 份（每份一个时间戳目录），可通过
`BACKUP_RETENTION_COUNT` 环境变量调整。

> 建议再把 `backups/` 目录定期同步到异地存储（对象存储、另一台机器等），
> 单机备份只能防误删/误改，防不了硬盘/整机故障。这部分本项目暂未提供
> 现成脚本，需要按你实际使用的存储服务自行补充（比如在 `backup.sh` 跑完后
> 加一行 `rsync`/`rclone` 命令）。

> `sqlite3` 命令行工具需已安装（`deploy/backup.sh` 与 Update Job 备份依赖它）：
> `sqlite3 --version`；没有的话 `sudo apt install sqlite3`（Debian/Ubuntu）。

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
- 应用内近期日志（写入 `data/logs.db`，带级别/logger 过滤）：管理后台
  「实时日志」页面（`/logs`，需要登录），或 `GET /api/logs/*` 系列接口。
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

- **单实例、单 worker**：不要给 `uvicorn`/`gunicorn` 配置多进程，也不要在
  多台机器上同时跑这套代码指向同一份 `data/`（SQLite 文件锁 + 内存态 hub
  都不支持这种拓扑）。
- **Docker 仅可作进程外壳**：见「Docker 部署」；**不要**把 `docker pull`
  镜像当作本产品的店内交付真相。
  裸机/VM 上用 systemd + venv 仍是默认路径。
- **生产机无 Node / 无运行时前端构建**：日常升级走发行包 + Update Job；
  发版用 `scripts/publish_release.sh`（见第 7 节与 ADR 0011）。
- **`WorkingDirectory` 必须是应用根目录**：`services/recipes/store.py` 的
  默认数据库路径 `data/recipes.db` 是相对路径，依赖进程 cwd。
- CORS（`main.py` 硬编码 `allow_origins=["*"]`）和爬虫营业时间
  （`scraper/adapter.py` 硬编码 07:30–21:30）目前都不支持环境变量覆盖，
  属于代码常量，改动需要改代码而不是这份部署配置。
