# `consolidate_dbs.py` 回滚说明

本脚本只**读**旧的多库 `.db` 文件，从不写入、不删除、不修改它们（包括其
`-wal`/`-shm` 侧车文件）。所有写入动作只发生在目标单库 `data/app.db` 上。
因此回滚非常简单，任选其一：

## 方式一：直接删除 app.db（最简单，等价于"从未迁移过"）

```bash
# 停应用后执行
rm -f data/app.db data/app.db-wal data/app.db-shm
```

旧的多库文件（`data/orders.db`、`data/dish_stations.db`、`data/auth.db`、
`data/recipes.db` ……）原样保留，未被本脚本改动过一个字节。若迁移完成并已手动删除旧分库文件，则只能依赖方式二（备份覆盖）或从异地备份恢复。

## 方式二：用迁移前的备份覆盖回去

脚本在**正式迁移**（非 `--dry-run`）开始前，若发现 `data/app.db` 已经存在
（例如新代码已经启动过、写入了少量新数据），会先把它备份为：

```
data/app.db.bak.<YYYYmmdd_HHMMSS>
data/app.db.bak.<YYYYmmdd_HHMMSS>-wal   # 若存在
data/app.db.bak.<YYYYmmdd_HHMMSS>-shm   # 若存在
```

回滚时：

```bash
# 停应用后执行，<timestamp> 替换为实际备份文件名中的时间戳
cp data/app.db.bak.<timestamp> data/app.db
cp data/app.db.bak.<timestamp>-wal data/app.db-wal 2>/dev/null || true
cp data/app.db.bak.<timestamp>-shm data/app.db-shm 2>/dev/null || true
```

## 关于 dry-run

`--dry-run` 模式下脚本只读取、只打印报告，不创建备份、不写入 `app.db`、
不建 schema——不存在"回滚 dry-run"这一说，运行前后磁盘上没有任何变化。

## 关于幂等重跑

脚本设计为幂等：对已经成功迁移过数据的表，重新执行脚本会检测到目标表已有
数据并整表跳过（见脚本头部"安全设计"第 3 点），不会产生重复行，也不会覆盖
已有数据。因此重复执行本身是安全的，通常不需要回滚，只有在需要真正撤销、
让系统重新回到"多库"架构时才需要用上面两种方式之一。
