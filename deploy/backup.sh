#!/usr/bin/env bash
# 单机冷备入口 —— 只负责调度与退出码，应用侧逻辑全部走备份服务
# （scripts/cold_backup.py → services/backup_service.py）。
#
# 每次运行产出一份可校验的单一归档（库快照 + 凭据 + 密钥 + 两类卫生照片 +
# 清单 + 校验和），保留最近 N 份并在固定位置写一份状态文件，供管理后台
# 「备份中心」读取最近一次冷备结论。SQLite 门店走官方 backup API、PostgreSQL
# 门店走整库 pg_dump，都不需要停服务、不引入停写窗口。
#
# 用法（在仓库根目录执行，或让 systemd timer / cron 调用，见文末示例）：
#   ./deploy/backup.sh
#
# 可覆盖的环境变量（可写进 deploy/luyun-backup.service 的 Environment=，
# 或调用前 export，或直接在命令前临时指定）：
#   DATA_DIR      源数据目录，默认 ./data（与 config.py 的 DATABASE_DIR 一致）
#   BACKUP_DIR    冷备输出根目录，默认 ./backups
#   PYTHON_BIN    执行用的 Python，默认 python3（应指向部署时使用的解释器）
#
# 保留份数不再写死在这里：默认读取运行配置中的「冷备保留份数」
# （管理后台「备份中心 → 保留与清理」可改，默认 14、上限 90）。也可用
#   ./deploy/backup.sh --retention 30
# 临时覆盖一次。
#
# 归档内含 .cred_key（凭据加密密钥），请确保 backups/ 目录权限受控。
#
# 退出码：0 成功；非 0 失败。失败时同样写状态文件，且不留下半成品归档。

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "❌ 未找到 ${PYTHON_BIN}，请设置 PYTHON_BIN 指向部署使用的解释器" >&2
  exit 1
fi

exec "$PYTHON_BIN" scripts/cold_backup.py "$@"

# ─────────────────────────────────────────────────────────────────
# 若不用 systemd timer（见 deploy/luyun-backup.service +
# deploy/luyun-backup.timer），也可以直接用 cron。示例：每天凌晨 3:10 执行，
# 用 `crontab -e`（建议用部署用户，而不是 root）添加：
#
#   10 3 * * * cd /opt/luyun && ./deploy/backup.sh >> ./backups/backup.log 2>&1
# ─────────────────────────────────────────────────────────────────
