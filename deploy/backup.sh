#!/usr/bin/env bash
# 单机 SQLite 冷备脚本 —— 对 data/ 目录下每个 *.db 用 `sqlite3 <db> ".backup <目标>"`
# 做在线安全备份（sqlite3 官方 backup API，即使数据库处于 WAL 模式且正被
# 应用写入，也能得到一致性快照，不需要停服务、不需要停 luyun.service）。
#
# 用法（在仓库根目录执行，或让 systemd timer / cron 调用，见文末示例）：
#   ./deploy/backup.sh
#
# 可覆盖的环境变量（可写进 deploy/luyun-backup.service 的 Environment=，
# 或调用前 export，或直接在命令前临时指定）：
#   DATA_DIR                源数据目录，默认 ./data（与 config.py 的
#                            DATABASE_DIR 默认值一致：仓库根目录下的 data/）
#   BACKUP_DIR               备份输出根目录，默认 ./backups
#   BACKUP_RETENTION_COUNT   每次运行后，最多保留多少份历史备份（按时间戳目录
#                            计，每份包含当次备份的全部 *.db），默认 14
#
# 除 *.db 外，若存在也会一并复制 data/credentials.enc（Fernet 加密的 POS 登录
# 凭据）与 data/.cred_key（加密密钥）到同一备份目录；备份目录含加密密钥，请
# 确保 backups/ 目录权限受控。
#
# 幂等性：每次运行都会创建一个新的、以当前时间戳命名的独立目录，不会覆盖或
# 依赖前一次运行的中间状态；重复执行不会破坏已有备份，失败时用 set -e 立即
# 退出并保留已完成的部分（不做“回滚删除”，避免误删已经备份成功的文件）。

set -euo pipefail
cd "$(dirname "$0")/.."

DATA_DIR="${DATA_DIR:-./data}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_COUNT="${BACKUP_RETENTION_COUNT:-14}"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "❌ 未找到 sqlite3 命令，请先安装（Debian/Ubuntu: sudo apt install sqlite3）" >&2
  exit 1
fi

if [[ ! -d "$DATA_DIR" ]]; then
  echo "❌ 数据目录不存在: ${DATA_DIR}" >&2
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${BACKUP_DIR}/${TIMESTAMP}"

shopt -s nullglob
db_files=("${DATA_DIR}"/*.db)
if [[ ${#db_files[@]} -eq 0 ]]; then
  echo "⚠️  未在 ${DATA_DIR} 下找到任何 .db 文件，跳过本次备份" >&2
  exit 0
fi

mkdir -p "$OUT_DIR"
echo "📦 备份 ${#db_files[@]} 个 SQLite 数据库 → ${OUT_DIR}"

for db in "${db_files[@]}"; do
  name="$(basename "$db")"
  target="${OUT_DIR}/${name}"
  if sqlite3 "$db" ".backup '${target}'"; then
    size="$(du -h "$target" 2>/dev/null | cut -f1)"
    echo "  ✅ ${name} → ${target} (${size})"
  else
    echo "  ❌ ${name} 备份失败" >&2
    exit 1
  fi
done

echo "🔐 备份 POS 凭据文件（若已配置）..."

if [[ -f "${DATA_DIR}/credentials.enc" ]]; then
  target="${OUT_DIR}/credentials.enc"
  cp "${DATA_DIR}/credentials.enc" "$target"
  chmod 600 "$target"
  size="$(du -h "$target" 2>/dev/null | cut -f1)"
  echo "  ✅ credentials.enc → ${target} (${size})"
else
  echo "  ⏭️  未找到 credentials.enc，跳过"
fi

if [[ -f "${DATA_DIR}/.cred_key" ]]; then
  target="${OUT_DIR}/.cred_key"
  cp "${DATA_DIR}/.cred_key" "$target"
  chmod 600 "$target"
  size="$(du -h "$target" 2>/dev/null | cut -f1)"
  echo "  ✅ .cred_key → ${target} (${size})"
else
  echo "  ⏭️  未找到 .cred_key，跳过"
fi

echo "🧹 应用保留策略：仅保留最近 ${RETENTION_COUNT} 份，清理更旧的备份目录..."
# 时间戳目录名按字典序排序即为时间顺序（YYYYMMDD_HHMMSS），sort -r 后最新的在最前面，
# tail -n +(RETENTION_COUNT+1) 取出「保留份数之外」的旧目录逐个删除。
# 不用 mapfile，是为了兼容更旧的 bash（如 macOS 系统自带的 3.2，无 mapfile 内建命令）。
old_dirs=()
while IFS= read -r old_dir; do
  old_dirs+=("$old_dir")
done < <(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -name '[0-9]*_[0-9]*' \
  | sort -r | tail -n "+$((RETENTION_COUNT + 1))")

for old_dir in "${old_dirs[@]:-}"; do
  [[ -n "$old_dir" ]] || continue
  echo "  🗑️  删除旧备份: ${old_dir}"
  rm -rf "$old_dir"
done

echo "✅ 备份完成: ${OUT_DIR}"

# ─────────────────────────────────────────────────────────────────
# 若不用 systemd timer（见 deploy/luyun-backup.service +
# deploy/luyun-backup.timer），也可以直接用 cron。示例：每天凌晨 3:10 执行，
# 用 `crontab -e`（建议用部署用户，而不是 root）添加：
#
#   10 3 * * * cd /opt/luyun && ./deploy/backup.sh >> ./backups/backup.log 2>&1
# ─────────────────────────────────────────────────────────────────
