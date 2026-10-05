/** Staff-phone and admin-queue task flow. Domain rules stay on the server. */

import { formatHygieneShortStamp, formatHygieneStamp } from './hygieneTime'

export const STATUS_TODO = '待拍'
export const STATUS_PENDING = '待验收'
export const STATUS_PASSED = '已通过'
export const STATUS_FIX_TODO = '待回拍'

export const QUEUE_BUCKETS = [
  { id: 'overdue', label: '已超时' },
  { id: 'soon', label: '快到点' },
  { id: 'todo', label: '还没完成' },
  { id: 'waiting', label: '等验收' },
]

const MINUTE_MS = 60 * 1000
const HOUR_MS = 60 * MINUTE_MS

export function isPassed(row) {
  return Boolean(row && row.status === STATUS_PASSED)
}

export function openRows(rows) {
  return (rows || []).filter((row) => !isPassed(row))
}

export function passedRows(rows) {
  return (rows || []).filter((row) => isPassed(row))
}

export function dailyProgress(items) {
  const list = Array.isArray(items) ? items : []
  let passed = 0
  let pending = 0
  let todo = 0
  for (const row of list) {
    if (row.status === STATUS_PASSED) passed += 1
    else if (row.status === STATUS_PENDING) pending += 1
    else todo += 1
  }
  const total = list.length
  return { total, passed, pending, todo, remaining: total - passed }
}

export function groupByZone(rows) {
  const zones = []
  const byId = new Map()
  for (const row of rows || []) {
    if (!byId.has(row.zone_id)) {
      const zone = { id: row.zone_id, name: row.zone_name, rows: [] }
      byId.set(row.zone_id, zone)
      zones.push(zone)
    }
    byId.get(row.zone_id).rows.push(row)
  }
  return zones
}

function sameDaily(left, right) {
  return Boolean(
    left
      && right
      && Number(left.item_id) === Number(right.item_id)
      && left.shift === right.shift,
  )
}

export function nextShootRow(rows, current) {
  const open = (rows || []).filter((row) => row.status === STATUS_TODO)
  if (!open.length) return null
  if (!current) return open[0]
  const sameZone = open.filter((row) => (
    Number(row.zone_id) === Number(current.zone_id) && !sameDaily(row, current)
  ))
  return sameZone[0] || open.find((row) => !sameDaily(row, current)) || null
}

export function nextDeepShootRow(rows, current) {
  const open = (rows || []).filter((row) => row.status === STATUS_TODO)
  if (!current) return open[0] || null
  return open.find((row) => Number(row.item_id) !== Number(current.item_id)) || null
}

export function nextFixWorkRow(rows, current, { isManager } = {}) {
  const list = rows || []
  const waiting = list.filter((row) => (
    row.status === STATUS_FIX_TODO && Number(row.id) !== Number(current && current.id)
  ))
  if (waiting.length) return waiting[0]
  if (!isManager) return null
  return list.find((row) => (
    row.status === STATUS_PENDING && Number(row.id) !== Number(current && current.id)
  )) || null
}

export function tabWorkCount(tabId, { inbox = [], deepInbox = [], fixInbox = [] } = {}) {
  // 待办页只列日常，角标跟同一口径走：三项相加会出现「角标 3、页面上只有 1 项」
  // 这种对不上的情况。专项、整改各自有 tab，角标也各自算。
  if (tabId === 'inbox') return openRows(inbox).length
  if (tabId === 'deep') return openRows(deepInbox).length
  if (tabId === 'fix') return (fixInbox || []).length
  return 0
}

export function workJumps({ deepRemaining = 0, fixRemaining = 0 } = {}) {
  const jumps = []
  if (deepRemaining) jumps.push({ tab: 'deep', label: `专项还有 ${deepRemaining} 项` })
  if (fixRemaining) jumps.push({ tab: 'fix', label: `整改还有 ${fixRemaining} 张` })
  return jumps
}

export function shiftClock(shift, clocks) {
  if (!clocks) return ''
  if (shift === '夜班') return clocks.night_hhmm || ''
  if (shift === '白班') return clocks.day_hhmm || ''
  return ''
}

/** @deprecated 新代码用 `hygieneTime.formatHygieneStamp`；旧名保留给既有调用方。 */
export function formatStamp(iso) {
  return formatHygieneStamp(iso)
}

export function deadlineUrgency(iso, now = Date.now()) {
  const stamp = Date.parse(iso)
  if (!Number.isFinite(stamp)) return 'none'
  const delta = stamp - now
  if (delta <= 0) return 'overdue'
  if (delta <= 30 * 60 * 1000) return 'soon'
  return 'ok'
}

export function statusTone(status) {
  if (status === STATUS_PASSED) return 'done'
  if (status === STATUS_PENDING) return 'pending'
  if (status === STATUS_FIX_TODO) return 'fix'
  return 'todo'
}

/** 这一项交过了、还在等验收（ADR 0071：待验收期间再交一张会替换上一张）。
 *
 *  它跟"谁能验收"是两个问题：**交这张的人**永远可以重拍自己那份待验收的提交，
 *  普通员工也一样。所以下面那三个 `*PrimaryAction` 收档位、这一条不收 —— 页面上
 *  那颗「重拍」按钮用它，别拿 `*PrimaryAction === 'review'` 去判，否则普通员工的
 *  重拍入口会跟着「对照」一起消失。 */
export function isPendingReview(row) {
  return Boolean(row) && row.status === STATUS_PENDING
}

/** 日常 / 专项那一行"这个人接下来该做什么"：**档位判据只有这一处**。
 *
 *  待验收那一档分两种人（`isManager`）：
 *  - 管理员 → `'review'`：打开对照，能通过 / 驳回；
 *  - 普通员工 → `'view'`：只能打开看（验收是管理员的事）。
 *
 *  以前这两个函数不看 `isManager`，同一行于是在**两处**各判一次：任务卡按它们渲染
 *  「对照」按钮，而队列文案（`buildWorkQueue` 的 `primaryLabel`）自己又写了一遍
 *  `isManager`。两处一旦分叉，普通员工就会点到一个既没有决定按钮、也没有说明的空壳
 *  面板（2026-10-05 审查 F-04）。所以：**档位判据留在这里一处**，按钮文案也好、页面
 *  行为也好，全部从这里的返回值派生（`queueActionLabel` 查表、`HygieneHomeView` 的
 *  `dailyAction`/`deepAction` 转发）。
 *
 *  `fixPrimaryAction` 是同一套形状，只是它没有 `'view'` 那一档：整改单对普通员工的
 *  主动作本来就是「回拍」，不需要再分一层。
 */
export function dailyPrimaryAction(row, { isManager } = {}) {
  if (!row || row.status === STATUS_PASSED) return 'none'
  if (row.status === STATUS_PENDING) return isManager ? 'review' : 'view'
  return 'shoot'
}

export function deepPrimaryAction(row, { isManager } = {}) {
  if (!row || row.status === STATUS_PASSED) return 'none'
  if (row.status === STATUS_PENDING) return isManager ? 'review' : 'view'
  return 'shoot'
}

export function fixPrimaryAction(row, { isManager } = {}) {
  if (!row) return 'none'
  if (row.status === STATUS_PENDING && isManager) return 'review'
  return 'reshoot'
}

/** 队列上那颗动作按钮的文案：**只按上面某个判据的返回值查表**，不再各写一遍。
 *
 *  `buildWorkQueue` 原来在自己的循环里又写了一次「待验收 && isManager」，判据于是有了
 *  第二份，迟早跟 `*PrimaryAction` 分叉 —— F-04 就是它（队列说「查看」、按钮说「对照」）。
 *  任务卡上那颗行内按钮用另一套更口语的词（把验收写成「对照」），但**判据同样是那三个
 *  函数**，两边只是各自挑文案；表里没有的组合给空串。
 */
const QUEUE_ACTION_LABELS = {
  daily: { shoot: '拍摄', review: '验收', view: '查看' },
  deep: { shoot: '拍前后', review: '验收', view: '查看' },
  fix: { reshoot: '回拍', review: '验收', view: '查看' },
}

export function queueActionLabel(kind, action) {
  const table = QUEUE_ACTION_LABELS[kind] || {}
  return table[action] || ''
}

function nextDateString(dateText) {
  const matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateText || ''))
  if (!matched) return ''
  const date = new Date(`${matched[1]}-${matched[2]}-${matched[3]}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + 1)
  return date.toISOString().slice(0, 10)
}

export function clockDueAt(hhmm, businessDate) {
  const clock = /^(\d{2}):(\d{2})$/.exec(String(hhmm || ''))
  const date = /^(\d{4}-\d{2}-\d{2})$/.exec(String(businessDate || ''))
  if (!clock || !date) return ''
  const dueDate = Number(clock[1]) < 6 ? nextDateString(date[1]) : date[1]
  if (!dueDate) return ''
  return `${dueDate}T${clock[1]}:${clock[2]}:00+08:00`
}

export function deadlineText(iso, now = Date.now()) {
  const stamp = Date.parse(iso)
  if (!Number.isFinite(stamp)) return ''
  const delta = stamp - now
  if (delta <= 0) {
    const minutes = Math.max(1, Math.ceil(-delta / MINUTE_MS))
    if (minutes < 60) return `已超时 ${minutes} 分钟`
    const hours = Math.ceil(minutes / 60)
    if (hours < 24) return `已超时 ${hours} 小时`
    return `已超时 ${Math.ceil(hours / 24)} 天`
  }
  if (delta <= 30 * MINUTE_MS) {
    return `还剩 ${Math.max(1, Math.ceil(delta / MINUTE_MS))} 分钟`
  }
  if (delta <= 24 * HOUR_MS) {
    return `还剩 ${Math.max(1, Math.ceil(delta / HOUR_MS))} 小时`
  }
  const short = formatHygieneShortStamp(iso)
  return short ? `${short} 前` : ''
}

function queueBucket(dueAt, status, now) {
  if (status === STATUS_PENDING) return 'waiting'
  const urgency = deadlineUrgency(dueAt, now)
  if (urgency === 'overdue') return 'overdue'
  if (urgency === 'soon') return 'soon'
  return 'todo'
}

function queuePriority(kind, bucket) {
  const offsets = {
    overdue: { fix: 0, daily: 10, deep: 20 },
    soon: { fix: 30, daily: 40, deep: 50 },
    todo: { fix: 60, daily: 70, deep: 80 },
    waiting: { fix: 90, daily: 100, deep: 110 },
  }
  return (offsets[bucket] && offsets[bucket][kind]) || 999
}

function queueTask({
  kind,
  row,
  key,
  title,
  context,
  typeLabel,
  status,
  dueAt,
  primaryLabel,
  now,
  rejected = false,
  rejectReason = '',
}) {
  const bucket = queueBucket(dueAt, status, now)
  const dueText = bucket === 'waiting'
    ? '已交，等验收'
    : deadlineText(dueAt, now)
  return {
    kind,
    row,
    key,
    title,
    context,
    typeLabel,
    status,
    dueAt,
    dueText,
    primaryLabel,
    bucket,
    bucketLabel: (QUEUE_BUCKETS.find((item) => item.id === bucket) || {}).label || '',
    priority: queuePriority(kind, bucket),
    // 这一项今天被打回过：员工端要明说，否则他只会看到状态回到"待拍"并原样重拍。
    rejected: Boolean(rejected),
    // 验收人写的原因（可能为空）：显示出来才知道该改什么。
    rejectReason: rejectReason || '',
  }
}

export function buildWorkQueue({
  inbox = [],
  deepInbox = [],
  fixInbox = [],
  shiftDue = '',
  deepDue = '',
  now = Date.now(),
  isManager = false,
  pendingKeys = null,
} = {}) {
  const tasks = []
  // 已经入队、还没确认上传成功的任务先从待办里拿掉：3G 下一张图要传几十秒，
  // 不拿掉的话员工切回待办会看到"还没拍"，然后重拍一遍。
  const pending = pendingKeys instanceof Set ? pendingKeys : null
  const isPending = (key) => Boolean(pending && pending.has(key))

  for (const row of openRows(inbox)) {
    const key = `daily:${row.item_id}:${row.shift}`
    if (isPending(key)) continue
    const dueAt = clockDueAt(shiftDue, row.business_date)
    tasks.push(queueTask({
      kind: 'daily',
      row,
      key,
      title: row.item_name,
      context: `${row.zone_name} · ${row.shift}`,
      typeLabel: '日常',
      status: row.status,
      dueAt,
      // 文案从判据的返回值派生，别在这里再写一遍 `isManager`（见 `dailyPrimaryAction`）。
      primaryLabel: queueActionLabel('daily', dailyPrimaryAction(row, { isManager })),
      now,
      rejected: row.rejected,
      rejectReason: row.reject_reason,
    }))
  }

  for (const row of openRows(deepInbox)) {
    const key = `deep:${row.item_id}`
    if (isPending(key)) continue
    const dueAt = clockDueAt(deepDue, row.business_date)
    tasks.push(queueTask({
      kind: 'deep',
      row,
      key,
      title: row.item_name,
      context: '专项卫生 · 前后对照',
      typeLabel: '专项',
      status: row.status,
      dueAt,
      primaryLabel: queueActionLabel('deep', deepPrimaryAction(row, { isManager })),
      now,
      rejected: row.rejected,
      rejectReason: row.reject_reason,
    }))
  }

  for (const row of (fixInbox || [])) {
    const key = `fix:${row.id}`
    if (isPending(key)) continue
    tasks.push(queueTask({
      kind: 'fix',
      row,
      key,
      title: `${row.zone_name} · ${row.ticket_type}`,
      context: row.body_text || '整改单',
      typeLabel: '整改',
      status: row.status,
      dueAt: row.deadline,
      primaryLabel: queueActionLabel('fix', fixPrimaryAction(row, { isManager })),
      now,
      rejected: row.rejected,
      rejectReason: row.reject_reason,
    }))
  }

  const bucketOrder = new Map(QUEUE_BUCKETS.map((item, index) => [item.id, index]))
  return tasks.sort((left, right) => {
    const bucket = bucketOrder.get(left.bucket) - bucketOrder.get(right.bucket)
    if (bucket) return bucket
    if (left.priority !== right.priority) return left.priority - right.priority
    const leftDue = Date.parse(left.dueAt)
    const rightDue = Date.parse(right.dueAt)
    if (Number.isFinite(leftDue) && Number.isFinite(rightDue) && leftDue !== rightDue) {
      return leftDue - rightDue
    }
    return String(left.key).localeCompare(String(right.key))
  })
}

export function queueGroups(tasks) {
  const list = Array.isArray(tasks) ? tasks : []
  return QUEUE_BUCKETS
    .map((bucket) => ({
      ...bucket,
      rows: list.filter((task) => task.bucket === bucket.id),
    }))
    .filter((bucket) => bucket.rows.length)
}

export function nextAfterRemove(items, removed, keyFn) {
  const list = Array.isArray(items) ? items : []
  if (!list.length || !removed) return null
  const key = keyFn(removed)
  const idx = list.findIndex((row) => keyFn(row) === key)
  const remaining = list.filter((row) => keyFn(row) !== key)
  if (!remaining.length) return null
  if (idx < 0) return remaining[0]
  return remaining[Math.min(idx, remaining.length - 1)]
}

export function dailyQueueKey(row) {
  return `${row.item_id}-${row.shift}`
}

export function deepQueueKey(row) {
  return String(row.item_id)
}

export function fixQueueKey(row) {
  return String(row.id)
}
