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

export function dailyPrimaryAction(row) {
  if (!row || row.status === STATUS_PASSED) return 'none'
  if (row.status === STATUS_PENDING) return 'review'
  return 'shoot'
}

export function deepPrimaryAction(row) {
  if (!row || row.status === STATUS_PASSED) return 'none'
  if (row.status === STATUS_PENDING) return 'review'
  return 'shoot'
}

export function fixPrimaryAction(row, { isManager } = {}) {
  if (!row) return 'none'
  if (row.status === STATUS_PENDING && isManager) return 'review'
  return 'reshoot'
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
      primaryLabel: row.status === STATUS_PENDING
        ? (isManager ? '验收' : '查看')
        : '拍摄',
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
      primaryLabel: row.status === STATUS_PENDING
        ? (isManager ? '验收' : '查看')
        : '拍前后',
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
      primaryLabel: fixPrimaryAction(row, { isManager }) === 'review' ? '验收' : '回拍',
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
