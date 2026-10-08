/**
 * 加班与补钟（票 01）的界面口径：**1 格 = 0.5 小时**。
 *
 * 库里存的就是「半小时数」（`half_hours`，带符号的整数），接口原样下发 —— 所以这一层
 * 只跟整数打交道，不做小时与分钟的换算，也就不存在浮点误差（`docs/adr/0100`）。
 *
 * 两个按钮是 `+` / `−`，没有「选加班还是补钟」那一栏：**符号就是类型**。从 `+1` 一直
 * 按 `−` 会走到 `-1`：0 不是一笔登记（服务端会拒它），所以步进要跳过 0。
 *
 * 界面文案与判据都从服务端来：上限随 `/api/overtime/me` 的响应下发（`max_half_hours`）
 * —— 这里的常量只是还没读到响应时的兜底与测试的锚点，页面优先用响应里那个。
 */

/** 单条上限 ±12 小时（±24 格）。与 `services/overtime/ledger.py` 的 `HALF_HOURS_MAX` 同源。 */
export const HALF_HOURS_MAX = 24
/** 事由限长。与 `services/overtime/ledger.py` 的 `MAX_REASON` 同源。 */
export const MAX_REASON = 50
/** 驳回理由限长。与 `services/overtime/ledger.py` 的 `MAX_REJECT_REASON` 同源。 */
export const MAX_REJECT_REASON = 100

/** 加减一格（0.5 小时），跳过 0，卡在 ±上限。 */
export function stepHalfHours(current, delta, max = HALF_HOURS_MAX) {
  const next = Number(current) + Number(delta)
  if (next === 0) return delta > 0 ? 1 : -1
  if (next > max) return max
  if (next < -max) return -max
  return next
}

/** 带符号的小时数：`+6.5` / `−2`（整点不带小数，减号用全角，跟服务端文案同一个字符）。 */
export function formatHalfHours(halfHours) {
  const hours = Number(halfHours) / 2
  const sign = hours > 0 ? '+' : '−'
  const size = Math.abs(hours)
  return `${sign}${Number.isInteger(size) ? size : size.toFixed(1)}`
}

/** 员工能自己登记的两天：今天与昨天（自然日；判据在服务端，这里只是把按钮摆出来）。 */
export function entryDayOptions({ today, yesterday }) {
  return [
    { value: today, label: '今天' },
    { value: yesterday, label: '昨天' },
  ]
}

const ENTRY_STATUS_TEXT = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
  cancelled: '已撤回',
  voided: '已作废',
}

const ENTRY_STATUS_TONE = {
  pending: 'wait',
  approved: 'ok',
  rejected: 'bad',
  cancelled: 'mute',
  voided: 'mute',
}

/** 状态的中文。认不出的状态原样回 —— 不装懂，也不吞掉它（票 02 起会出现更多状态）。 */
export function entryStatusText(status) {
  return ENTRY_STATUS_TEXT[status] || status || ''
}

export function entryStatusTone(status) {
  return ENTRY_STATUS_TONE[status] || 'mute'
}

/** 加班 / 补钟的中文。`kind` 是服务端按符号派生的，前端不自己判正负。 */
export function entryKindText(kind) {
  return kind === 'makeup' ? '补钟' : '加班'
}

/** 这条是不是**他自己**提的（`created_by` 的形状见 `EntryActor.stamp`）。 */
export function isSelfSubmitted(entry) {
  return entry?.created_by === `staff:${entry?.employee_id}`
}

/** 这条是谁提上来的：台账是工资依据，代录的那几笔必须一眼看得出来（票 02 的验收 4）。 */
export function submittedByText(entry) {
  if (isSelfSubmitted(entry)) return '本人提交'
  return entry?.created_by === 'super' ? '超级管理员代录' : '同事代录'
}
