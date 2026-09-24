-- 0009：换班（票 09）
--
-- 背景：请假（0008）只有一扇门 —— 员工提、店长批。换班多一道：**先过对方**。
-- 员工指一个同事和一天，对方在手机上同意之后才轮到店长；店长批了，两人那天的班对调
-- （责任区跟着各自的新班次走，见 `services/scheduling/store.py` 的 `_approve_swap`）。
-- 这套状态机在 `spec.md` 里早就定下来了（`pending_peer` 就是给它留的），申请本身复用
-- 0008 那张表（`kind = 'swap'`）——「谁提的、哪一天、到哪一步」跟请假是同一批字段，
-- 多出来的只有「对方是谁」。
--
-- 所以这一迁移只加一列：`peer_employee_id`（申请人想跟谁换）。请假那几条这一列为空。
-- **空值不等于换班**：换班认 `kind = 'swap'`，那一列只说「跟谁」。
--
-- 对方要读「等我的换班」（`peer_employee_id = ? AND status = ?`），索引就落在这两列上 ——
-- 前导列跟着访问路径走（0007 定下的口径），不是跟着 `tenant_id`（单店阶段代码里没有租户条件）。
-- 申请人自己那两条访问路径仍在 0008 的 `idx_scheduling_requests_employee` 上。
--
-- 幂等：IF NOT EXISTS，可重复执行。**依赖 0008**：那张表不在时这条 ALTER 会报错 ——
-- 按序号应用（0008 在前）即可，Admin 的迁移面板就是这么跑的。

ALTER TABLE scheduling_requests
    ADD COLUMN IF NOT EXISTS "peer_employee_id" BIGINT;

CREATE INDEX IF NOT EXISTS idx_scheduling_requests_peer
    ON scheduling_requests (peer_employee_id, status);

-- 同一对、同一天，只允许挂一条**没落定**的换班申请。
-- 服务层在 `submit_swap` 里已经扫过一遍（要给人话），但那次扫描在写锁外 —— 手快点了两下
-- 或者两个调用方同时进来，还是会有第二条挤进去：对方手机上出现两张一样的卡，店长那儿的
-- 两张卡都批等于又换了回去。这条局部唯一索引是最后一道闸，撞上它就报 `already_asked`。
-- 落定的（approved / rejected / cancelled）不在索引范围内：过些日子可以再换一次。
CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduling_requests_swap_once
    ON scheduling_requests (employee_id, peer_employee_id, start_date)
    WHERE kind = 'swap' AND status IN ('pending_peer', 'pending_manager');
