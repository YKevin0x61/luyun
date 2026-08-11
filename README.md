# LuckIn 订单系统（luyun）

餐厅订单采集与查询系统，服务 **LuckIn**。从 POS 定时抓取点菜数据，落本地 SQLite，提供管理后台、厨房显示（KDS）与 REST API。

**技术栈：** FastAPI · SQLite（WAL）· Playwright · Vue3 Admin · uni-app KDS  

**交付方式：** 公开 GitHub Release **发行包**（应用树 + 预构建前端 + 版本清单）。店内机器不装 Node、不 clone、不强制 PAT。升级走后台「系统更新」，不是 `git pull` / `docker pull`。

| 能力 | 说明 |
|------|------|
| 订单采集 | 营业时段轮询 POS，退菜检测，档口映射 |
| 管理后台 | 仪表盘、数据管理、销售报表、备货、企微推送、配方 SOP |
| 厨房 KDS | WebSocket 驱动，断连告警，打印队列 |
| 系统更新 | 版本检测 → 环境自检 → 应用发行包 |

---

## 部署

单机、单实例、**单 uvicorn worker**。不要多 worker、不要多机共用同一份 `data/`。

### 方式 A：systemd（推荐）

公开仓一键装最新正式版（默认目录 `/opt/luyun`）：

```bash
curl -fsSL \
  -L https://github.com/YKevin0x61/luyun/releases/latest/download/install.sh \
  | sudo bash
```

完成后人工步骤：

```bash
# 1) 编辑环境变量（至少填 LUYUN_CRED_KEY 等）
sudo vim /opt/luyun/deploy/env.production
sudo chmod 600 /opt/luyun/deploy/env.production

# 2) 反代 + TLS（任选其一）
#    deploy/Caddyfile 或 deploy/nginx.conf
#    把 /api、/ws 转到 127.0.0.1:8000，Admin 静态指向 admin-web/dist

# 3) 启动
sudo systemctl start luyun.service
systemctl status luyun
curl -s http://127.0.0.1:8000/api/system/health

# 4) 浏览器
#    /login  建管理员
#    /setup  填 POS 凭据；同一页可做「系统更新」
```

定点版本：把 URL 里的 `latest` 换成 tag，或设 `LUYUN_TAG=vX.Y.Z`。  
本地已有脚本时：`./scripts/bootstrap_install.sh --repo YKevin0x61/luyun --tag vX.Y.Z --deploy-dir /opt/luyun`

### 方式 B：Docker / Compose（进程外壳）

镜像只提供运行时；应用树仍是发行包。升级仍走 Admin「系统更新」。

```bash
git clone https://github.com/YKevin0x61/luyun.git
cd luyun
cp deploy/.env.docker.example deploy/.env.docker   # 按需改端口
./scripts/docker_up.sh
```

**必须挂父目录**（默认 `deploy/runtime` → 容器 `/srv/luyun`），直播树是其中的 `app/`。不要把 `runtime/app` 单独挂成 volume，否则系统更新无法原子切换目录。

| 宿主机（默认） | 容器 |
|----------------|------|
| `deploy/runtime/` | `/srv/luyun` |
| `deploy/runtime/app/` | `/srv/luyun/app` |
| `/var/run/docker.sock` | `/var/run/docker.sock` |

首次 `app/` 为空时会自动拉取最新正式发行包。之后编辑 `deploy/runtime/app/deploy/env.production`，反代指向 `127.0.0.1:8000`（或 `.env.docker` 里的端口）。

### 日常升级（两种部署通用）

1. 开发者发版：`./scripts/publish_release.sh vX.Y.Z`
2. 店内：登录后台 → `/setup` → **系统更新** → 检测版本 → 应用更新  
3. 旁路作业：备份 → 下载并校验发行包 → 原子切换 → 条件 pip → 重启

公开仓无需 GitHub PAT；限流时可在「系统更新 → GitHub 连接」选填只读 Token。

### 备份（建议）

见 `deploy/backup.sh` 与 `luyun-backup.timer`。systemd 路径可按 `deploy/README.md` 启用每日冷备。

---

## 本地开发（可选）

店内部署不需要本节。开发机：

```bash
pip install -r requirements.txt
playwright install chromium
cd admin-web && npm ci && npm run build && cd ..
python3 scripts/start.py
# 或：uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

| 地址 | 用途 |
|------|------|
| http://localhost:8000/admin/ | 管理后台 |
| http://localhost:8000/kds/ | 厨房显示 |
| http://localhost:8000/docs | API 文档 |
| http://localhost:8000/setup | POS 凭据 / 系统更新 |

Admin 热更新：`cd admin-web && npm run dev`（`:5173`，代理 `/api`、`/ws`）。  
测试：`pytest tests/` · `cd admin-web && npm run test`

---

## 更多文档

| 文档 | 内容 |
|------|------|
| [docs/RELEASE_AND_DEPLOY.md](docs/RELEASE_AND_DEPLOY.md) | 发版与部署操作规范 |
| [deploy/README.md](deploy/README.md) | 反代、备份、Docker 细节 |
| [docs/release-asset-layout.md](docs/release-asset-layout.md) | 发行包附件契约 |
| [AGENTS.md](AGENTS.md) | 仓库架构与开发约定 |
| [CONTEXT.md](CONTEXT.md) | 领域词汇 |
| [docs/adr/0011-release-bundle-update.md](docs/adr/0011-release-bundle-update.md) | 发行包交付决策 |

---

## 许可证

[MIT](./LICENSE) © 2026 LuckIn
