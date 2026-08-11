# 发行规范与部署流程

本文是店内部署与发版的**操作规范**（对齐 [ADR 0011](./adr/0011-release-bundle-update.md)、`CONTEXT.md`「部署与更新」）。  
[ADR 0010](./adr/0010-github-release-update-job.md)（git checkout + 分拆前端 tar）已被 0011 **取代**，勿再按旧契约操作。  
组件级细节（反代、备份 timer 等）见 [`deploy/README.md`](../deploy/README.md)；发行包附件契约见 [`release-asset-layout.md`](./release-asset-layout.md)。

---

## 1. 总原则


| 原则 | 说明 |
| --- | --- |
| 交付物只有 **正式 GitHub Release** | 以正式 Release 宣告为准；运行实例**不跟随** branch tip |
| 店内交付物是 **发行包 (Release Bundle)** | 单一归档：应用树 + 预构建 Admin/KDS + 版本清单 + 校验材料；装机与升级同款 |
| 前端**只在发版机构建** | 店内机器**不装 Node**、不跑 `npm` / `uni` / `build_kds.sh` |
| 已装身份以 **版本清单** 为准 | 不以部署目录 git tag / 单独 `APP_VERSION` 作为「已装发行版」权威 |
| 日常升级走管理后台 | `/setup` →「系统更新」；真正干活的是旁路 **更新作业**（systemd oneshot 或 Docker 后台进程） |
| 单机单实例单 worker | 禁止多 uvicorn worker、禁止多机共用同一份 `data/`；Docker 可以是进程外壳，**不是**以镜像 pull 为交付真相 |
| 数据与代码分离 | 切换应用树时保留 `data/`（库、POS 凭据等）；更新前强制备份 |


### 角色与名词

（与 `CONTEXT.md` 词汇一致）


| 名词 | 谁做 | 含义 |
| --- | --- | --- |
| **发行版 (Release)** | 开发者发布 | 正式 GitHub Release（附带 tag）+ **发行包**等附件 |
| **发行包 (Release Bundle)** | 随 Release 发布 | `luyun-release-bundle.tar.gz`（+ `SHA256SUMS`） |
| **版本清单 (Release Manifest)** | 装入部署目录 | 本机已装发行版身份；版本检测以此为准 |
| **运行实例 (Runtime Instance)** | 店内那台机 | 正在跑的单机部署；仅通过更新环境自检才可应用更新 |
| **版本检测 (Version Check)** | 管理员在后台看 | 本机版本清单 vs 远端正式 Release + **更新环境自检**，不改代码 |
| **更新环境自检 (Update Preflight)** | 随版本检测 | 下载/校验/切换/重启条件的红绿灯；不满足则禁止应用更新 |
| **应用更新 (Apply Update)** | 管理员在后台点 | 选定目标发行版并发起更新（回滚=再装更旧正式版发行包） |
| **更新作业 (Update Job)** | oneshot / Docker 旁路 | 备份 → 下发行包并硬校验 → 旁路解压原子切换 → 条件 pip → 重启 |
| **引导安装 (Bootstrap Install)** | 装机时跑脚本 | 新机器用同款发行包装到「应用进程可启动」（公开仓匿名下载，无 Deploy Key / clone / 强制 PAT） |


---

## 2. 发行规范（开发者）



### 2.1 版本号规则

1. **发版对齐版本**：`config.py` 里的 `APP_VERSION`（裸 semver，如 `0.1.0`）。
2. **Git tag**：建议 `v` + 同号，如 `v0.1.0`。`publish_release.sh` 会去掉一层前导 `v`/`V` 后与 `APP_VERSION` 比对，**必须一致**。
3. **本机已装发行版**：以部署目录中的 **版本清单**（`RELEASE_MANIFEST.json`）为准；与远端该 tag 声明不一致时，版本检测按异常/非精确状态呈现——**不要**把 git tag 当作已装权威。
4. **只发正式 Release**：默认目录不含 prerelease；店内 Version Check 不展示预发布。
5. **一次 Release = 一次可回滚点**：回滚 = 对更旧正式 tag 再做一次 Apply Update（再下该版发行包）。



### 2.2 发版前检查清单

- [ ] 功能在开发机验证通过；相关测试已跑
- [ ] `config.py` 的 `APP_VERSION` 已改成即将发布的版本
- [ ] 工作区干净（无未提交改动）；需要进 Release 的提交已 push
- [ ] 已登录 GitHub CLI：`gh auth status`（账号对仓库有写 Release 权限）
- [ ] 发版机已装 Node/npm（Admin）、以及 KDS 构建所需工具（见 `scripts/build_kds.sh`）
- [ ] 先跑契约校验：`./scripts/publish_release.sh --dry-run vX.Y.Z`



### 2.3 发布步骤

在**干净**仓库根目录：

```bash
# 1) 确认版本已写入 config.py，例如 APP_VERSION = "0.2.0"

# 2) 干跑（不构建、不打 tag、不调 GitHub）
./scripts/publish_release.sh --dry-run v0.2.0

# 3) 正式发布：构建 Admin + KDS → 打发行包 → 打 annotated tag → gh release create
./scripts/publish_release.sh v0.2.0
```

脚本会：

1. 拒绝 dirty worktree、拒绝 `APP_VERSION` 与 tag 不对齐
2. `admin-web`: `npm ci && npm run build`
3. `./scripts/build_kds.sh`
4. 打包并上传发行包与校验材料（见下节）
5. 在当前 HEAD 打 tag（若 tag 已存在则必须已指向同一 HEAD）
6. `gh release create`（`--generate-notes`）

发布成功后，在 GitHub 仓库 Releases 页确认该 tag 下有发行包附件。

### 2.4 发行包附件契约（必须满足）

详见 [`release-asset-layout.md`](./release-asset-layout.md)。摘要：


| 附件名 | 角色 |
| --- | --- |
| `luyun-release-bundle.tar.gz` | **发行包**：应用树 + 预构建 Admin/KDS + `RELEASE_MANIFEST.json` |
| `SHA256SUMS` | 发行包硬校验 sidecar（校验失败则不得激活） |
| `install.sh` | `curl\|bash` 引导入口（同 `scripts/curl_install.sh`，内嵌 repo/tag） |


- 归档是**目录内容**的 tar.gz（不是外包一层同名文件夹）。  
- 解压后至少存在：`RELEASE_MANIFEST.json`、`admin-web/dist/index.html`、`public/kds/index.html`、`public/kds/assets/`、`requirements.txt`。  
- 缺发行包或校验材料时，Update Job / Bootstrap **必须失败**，不得半截装 UI。  
- 运行实例**禁止**用「在店内重新 build」代替下载发行包。  
- **已退役**：分拆的 `admin-web-dist.tar.gz` / `kds-dist.tar.gz` + git checkout **不再是**店内装机/升级路径（ADR 0010，已被 0011 取代）。



### 2.5 禁止事项（发版）

- 不把未打 tag 的 branch tip 当店内可升级目标  
- 不把 `admin-web/dist`、`public/kds` 当日常提交进 git 的交付方式（交付走发行包）  
- 不在店内机器上为日常升级执行 `npm run build` / `build_kds.sh`  
- 不把 `GITHUB_RELEASES_TOKEN`、`deploy/env.production`、`data/credentials.enc`、旧 Deploy Key 私钥提交进仓库



### 2.6 发版后通知店内

告知管理员：新正式 Release tag（如 `v0.2.0`）已发布 → 登录后台 → `/setup` →「系统更新」→ 版本检测（含更新环境自检）→ 应用更新。

---



## 3. 店内凭据（一次性准备）

仓库为**公开仓**。装机与日常升级默认**匿名**访问 GitHub Releases（**不需要 Deploy Key / 店内 `.git` / 强制 PAT**）。


| 项 | 用途 | 落盘位置 |
| --- | --- | --- |
| 仓库名 | 固定在 `config.py`（默认 `YKevin0x61/luyun`） | Bootstrap 可能写入 `deploy/env.production` 的 `GITHUB_REPO` |
| **可选**只读 PAT | 仅在 API 限流时提高额度 | `GITHUB_RELEASES_TOKEN`（env）或 `/setup` →「系统更新」→「GitHub 连接」（`data/github_release.enc`） |


公开仓装机不必创建 PAT。若日后需要 Token：权限收窄到本仓库 Contents/Metadata（及下载发行包等）只读即可；在管理后台更换后**无需重启**。  
示例字段说明见 `deploy/env.production.example`。

若机器上仍留有旧的 `secrets/github_deploy_key` / `GIT_SSH_COMMAND` / 已无用的 PAT，可忽略或手工清理；新 Bootstrap / 新 Update Job 路径不再依赖它们。

---



## 4. 新机器部署流程（Bootstrap）



### 4.1 目标与边界

**做到**：从选定正式 Release 下载并硬校验**同款发行包**、解压到部署目录、venv + Playwright、版本清单就位、`luyun` + `luyun-update` 单元已 enable（脚本默认不替你 start 主服务，以结束时打印为准）。

**不做**：反代 / TLS / 域名、POS 凭据填写、日常冷备 timer 启用、装 Node、Deploy Key、git clone。

### 4.2 一键安装（推荐：`curl | bash`）

公开仓直接下 `install.sh` / 发行包即可，**无需 PAT**。  
**默认安装最新正式 Release**（GitHub `releases/latest`，不含 prerelease）。命令：

```bash
curl -fsSL \
  -L https://github.com/YKevin0x61/luyun/releases/latest/download/install.sh \
  | sudo bash
```

可选：`LUYUN_DEPLOY_DIR`（默认 `/opt/luyun`）；`LUYUN_TAG=vX.Y.Z` 可改为定点版本（或把 URL 里的 `latest` 换成具体 tag）。  
若设置了 `GITHUB_RELEASES_TOKEN`，请用 `sudo -E bash` 把环境变量传给 root。

想再短：写进 `~/.bashrc` 后执行 `luyun-install`（默认最新）或 `luyun-install v0.1.1`：

```bash
luyun-install() {
  local ver="${1:-latest}"
  local path="latest/download"
  [[ "$ver" == "latest" ]] || path="${ver}/download"
  curl -fsSL \
    -L "https://github.com/YKevin0x61/luyun/releases/${path}/install.sh" \
    | sudo bash
}
```

### 4.3 本地已有脚本时

已拿到仓库脚本时可直接：

```bash
./scripts/bootstrap_install.sh \
  --repo YKevin0x61/luyun \
  --tag v0.1.0 \
  --deploy-dir /opt/luyun
```

非 root 时脚本会打印需 `sudo` 安装 systemd 单元的步骤。  
`--deploy-key-file` / `--git-url` 等旧选项已废弃并会直接报错。



### 4.4 引导安装完成后的人工清单

按脚本结束提示逐项做：

1. **编辑** `/opt/luyun/deploy/env.production`
  - 填齐 `LUYUN_CRED_KEY` 等（可用 Fernet 生成，见 env 示例注释）  
  - `GITHUB_RELEASES_TOKEN` 公开仓可留空；`GITHUB_REPO` 一般与代码默认一致  
  - `chmod 600 deploy/env.production`
2. **反代 + TLS**：Caddy（首选）或 Nginx，见 `deploy/README.md` §3；把 `/api`、`/ws` 转到 `127.0.0.1:8000`，Admin 静态指向 `admin-web/dist`。
3. **启动主服务**：
  ```bash
   sudo systemctl start luyun.service
   systemctl status luyun
   curl -s http://127.0.0.1:8000/api/system/status
  ```
4. **首次初始化**：浏览器打开 `/login` 建管理员账号；`/setup` 填 POS 凭据；同一页「系统更新」可做版本检测 / 更新环境自检（公开仓无需 PAT）。
5. **（建议）** 启用每日冷备 timer：`deploy/README.md` 备份章节。



### 4.5 部署验收

- [ ] `systemctl is-active luyun` 为 active  
- [ ] `curl -s http://127.0.0.1:8000/api/system/health` 正常  
- [ ] 经域名可打开管理后台并登录  
- [ ] `/setup`「系统更新」能列出远端正式 Release，且更新环境自检可读（公开仓匿名即可）  
- [ ] KDS `/kds` 可打开  
- [ ] POS 凭据配置后采集循环无鉴权错误（按营业时段）  

---



## 5. 日常升级流程（运行实例）



### 5.1 标准路径（唯一推荐）

```text
开发者 publish_release（产出发行包 + SHA256SUMS + install.sh）
        ↓
管理员登录 → /setup →「系统更新」
        ↓
版本检测（本机版本清单 / 可用列表 / 是否有更新）
        + 更新环境自检（红绿灯：重启能力、凭据、进行中作业、脏树等）
        ↓
仅通过更新环境自检的运行实例可应用更新；自检不通过则隐藏/禁用应用更新（脏树仅可在明确确认丢弃本地改动后继续）
        ↓
选择目标正式 Release（可新可旧）→ 确认
        ↓
若营业高峰：警告，需勾选覆盖后继续
        ↓
Apply Update（Web 只写意图；systemd：`systemctl start --no-block luyun-update`；Docker：后台跑 `scripts/run_update_job.py`）
        ↓
Update Job：备份 → 下载/校验发行包 → 旁路解压并原子切换 → 条件 pip → 重启（systemd：`luyun`；Docker：`docker.sock` 重启容器）
        ↓
页面轮询 data/update_job.json 至 succeeded / failed
```

主服务会短暂中断；WebSocket / 采集会随进程重启恢复。宜避开极端高峰；急事可覆盖警告。

**Docker / 1Panel（进程外壳）：** 用 `deploy/docker-compose.yml` / `./scripts/docker_up.sh`。  
必须绑定挂载**直播应用目录的父目录**（默认 `deploy/runtime` → `/srv/luyun`，直播树 `/srv/luyun/app`），并挂载 `/var/run/docker.sock`；设置 `LUYUN_DEPLOY_MODE=docker`、`LUYUN_DOCKER_CONTAINER=<容器名>`（与 `container_name` 一致）。详见 `deploy/README.md` §1.1。**不要求**挂载 `.git`；交付仍是发行包，不是 `docker pull` 镜像。

### 5.2 更新环境自检（Update Preflight）

版本检测会给出能否应用更新的只读结论（红/绿灯），例如：

- 可访问 GitHub Releases（公开仓无需 PAT）、能列出正式 Release 并下载发行包  
- 具备重启能力（systemd 路径，或 Docker 模式下的 socket + 容器名）  
- 无进行中的更新作业  
- 部署树默认须干净；脏树默认红灯，仅管理员明确确认丢弃本地改动后方可 Apply  

未通过更新环境自检的机器（典型：开发机 checkout）上，界面隐藏或禁用应用更新。

### 5.3 Update Job 阶段

阶段名对 Admin 轮询稳定（ADR 0011）：


| 阶段 | 含义 |
| --- | --- |
| `queued` | 已记录意图，oneshot / Docker 旁路作业待跑或刚启动 |
| `backing_up` | **强制备份**；失败则**不**改动线上应用树 |
| `fetching_bundle` | 下载 `luyun-release-bundle.tar.gz` + `SHA256SUMS` 并硬校验 |
| `installing` | 旁路解压、保留上一版目录后原子切换；不覆盖店内 `data/` / 凭据 |
| `syncing_deps` | 仅当版本清单 `requirements_fingerprint` 变化时 pip；否则跳过 |
| `restarting` | systemd：`systemctl restart luyun`；Docker：Engine API restart 容器 |
| `succeeded` / `failed` | 终态；失败且已离开旧树时切回上一版目录并尽量拉起主服务；`error` 含日志指针 |


并发：已有进行中的作业时，新的 Apply 会被拒绝。  
排障：`data/update_job.json`、`data/update_job.log`；systemd 另看 `journalctl -u luyun-update`。

### 5.4 回滚


| 类型 | 做法 |
| --- | --- |
| **软件回滚** | 「系统更新」对更旧正式 Release 再 Apply Update 一次（再装该版发行包） |
| **数据回滚** | `systemctl stop luyun` 后，用备份目录中的 `.db` / 凭据覆盖 `data/`，再 start（见 `deploy/backup.sh`） |




### 5.5 旧版 git 店面的迁移（无长期双轨）

若运行实例仍是 ADR 0010 时代的 **git checkout + 分拆前端资产** 布局：

- **下一次成功的 Apply Update**（新发行包作业）会写入 **版本清单**，此后版本检测以清单为已装权威。  
- **没有**长期「git Apply 与发行包 Apply 双轨并存」；迁移切口就是这一次成功的应用更新。  
- 迁移后不再需要 Deploy Key / 店内 `.git` 参与升级；Docker 进程外壳仍需要 socket + 容器名以便重启。

### 5.6 禁止的「升级」方式

- SSH 上生产机 `git pull` 主分支后当升级  
- 在生产机 `npm ci && npm run build` / `build_kds.sh`  
- 手工改代码不打 Release 却指望后台 Version Check 识别为正式发行版  
- 指望以 `docker pull` 镜像作为本产品的标准升级路径  

应急 SSH 仅用于 Admin 不可用时的排障；恢复后仍应回到发行包 +「系统更新」。

---



## 6. 端到端对照表


| 场景 | 入口 | 产出 |
| --- | --- | --- |
| 发新版本 | 开发机 `scripts/publish_release.sh vX.Y.Z` | GitHub Release + 发行包 + `SHA256SUMS` + `install.sh` |
| 新机器 | `curl …/install.sh \| sudo -E bash` + 人工反代/env/POS | 可启动的运行实例（版本清单已落盘） |
| 店内升级/软件回滚 | `/setup` →「系统更新」 | Update Job 切到目标发行版 |
| 看进度/失败 | 同页轮询；或 `data/update_job.*` / `journalctl -u luyun-update` | 阶段与错误信息 |
| 日常数据冷备 | `deploy/backup.sh` 或 backup timer | `backups/<timestamp>/` |


---



## 7. 相关文件索引


| 路径 | 用途 |
| --- | --- |
| `docs/adr/0011-release-bundle-update.md` | **现行**架构决策（发行包交付） |
| `docs/adr/0010-github-release-update-job.md` | 已取代：旧 git + 分拆前端资产决策（仅历史） |
| `docs/release-asset-layout.md` | 发行包附件契约 |
| `deploy/README.md` | 反代、备份、手工对照、约束 |
| `deploy/env.production.example` | 生产环境变量模板（含 GitHub Release 凭据） |
| `deploy/luyun.service` / `luyun-update.service` | 主服务 / 更新作业 |
| `scripts/publish_release.sh` | 发版（发行包 + 校验 + `install.sh`） |
| `scripts/curl_install.sh` / Release `install.sh` | `curl\|bash` 一键入口 |
| `scripts/bootstrap_install.sh` | 引导安装本体 |
| `scripts/run_update_job.py` | Update Job 执行体 |
| `api/release_update.py` / `services/release_update/` | 版本检测 · 更新环境自检 · 应用更新 · 作业状态 |


---



## 8. 快速命令备忘

```bash
# —— 开发机发版 ——
./scripts/publish_release.sh --dry-run v0.2.0
./scripts/publish_release.sh v0.2.0

# —— 新机器（一键，默认最新正式版；公开仓无需 PAT）——
curl -fsSL \
  -L https://github.com/YKevin0x61/luyun/releases/latest/download/install.sh \
  | sudo bash
sudo systemctl start luyun

# —— 新机器（Docker 进程外壳）——
cp deploy/.env.docker.example deploy/.env.docker
./scripts/docker_up.sh

# —— 店内升级 ——
# 浏览器: https://<域名>/setup → 系统更新

# —— 排障 ——
journalctl -u luyun -n 100 --no-pager
journalctl -u luyun-update -n 100 --no-pager
cat /opt/luyun/data/update_job.json
# Docker:
# docker compose -f deploy/docker-compose.yml --env-file deploy/.env.docker logs -f
```
