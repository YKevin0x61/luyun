// 工作台「任务」的纯展示口径（票 02，2026-11-11）。
//
// 只放「怎么把后端的字段读成人话」的函数，不发请求、不碰 Vue —— 任务卡、详情
// 弹层、发布弹层共用同一套文案，组件测试与页面走查才不会各说各的。

export const POOL_OPTIONS = [
  { value: 'all_staff', label: '全体在职员工' },
  { value: 'managers', label: '有管理能力的员工' },
  { value: 'on_duty', label: '当天当班的人' },
  { value: 'zone_on_duty', label: '当天在某工作区当班的人' },
]

export const DEADLINE_DEFAULT = '18:00'

const STATUS_LABELS = {
  todo: '进行中',
  pending: '待验收',
  passed: '已通过',
  cancelled: '已撤回',
}

/** 状态徽标文案。逾期的「进行中」直接说「已逾期」—— 卡片上看不出现在的钟点，
 *  只写「进行中」会让人以为还有时间。 */
export function statusLabel(task) {
  if (!task) return ''
  if (task.status === 'todo' && task.overdue) return '已逾期'
  return STATUS_LABELS[task.status] || String(task.status || '')
}

/** 状态色调（样式类名后缀）：逾期是危险色，待验收是提醒色，通过是安全色。 */
export function statusTone(task) {
  if (!task) return 'muted'
  if (task.status === 'todo' && task.overdue) return 'danger'
  if (task.status === 'pending') return 'warn'
  if (task.status === 'passed') return 'ok'
  if (task.status === 'cancelled') return 'muted'
  return 'action'
}

/** 截止时刻。按 ISO 文本切片，不 new Date()：跨时区跑测试或员工手机时区偏了，
 *  「9月24日 18:00」都还是店长发布时写下的那个钟点。 */
export function formatDeadlineLabel(iso) {
  const text = String(iso || '')
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
  if (!match) return '未设截止'
  return `${Number(match[2])}月${Number(match[3])}日 ${match[4]}:${match[5]}`
}

/** 只要时分（发布弹层编辑时回填用）。 */
export function deadlineHHMM(iso) {
  const match = String(iso || '').match(/[T ](\d{2}:\d{2})/)
  return match ? match[1] : ''
}

/** 指派摘要：小组列名字（超过 3 个折叠），池说池名 + 当天解析出的人数。 */
export function assigneeText(task) {
  if (!task) return ''
  const names = Array.isArray(task.assignee_names) ? task.assignee_names : []
  if (task.assignee_kind === 'group') {
    if (!names.length) return '未指定执行人'
    if (names.length <= 3) return names.join('、')
    return `${names.slice(0, 3).join('、')} 等 ${names.length} 人`
  }
  const label = task.pool_label || '指定池'
  const zone = task.pool_zone_name ? `「${task.pool_zone_name}」` : ''
  const people = names.length ? ` · ${names.length} 人` : ''
  return `${label}${zone}${people}`
}

/** 池任务在截止日解析不出人（发布时不拦截，只在页面上提示）。 */
export function assigneeEmptyHint(task) {
  if (!task || task.assignee_kind !== 'pool') return false
  return !(Array.isArray(task.assignee_names) && task.assignee_names.length)
}

/** 照片要求。 */
export function photoHint(task) {
  const count = Number(task && task.photo_count) || 1
  return `需拍 ${count} 张现场照`
}

/** 发布/编辑表单 → 后端 assignee 结构。 */
export function buildAssignee({ mode, groupIds = [], poolKind = 'all_staff', zoneId = null }) {
  if (mode === 'group') {
    return { kind: 'group', employee_ids: groupIds.map((id) => Number(id)).filter((id) => id > 0) }
  }
  return {
    kind: 'pool',
    pool_kind: poolKind,
    zone_id: poolKind === 'zone_on_duty' ? zoneId : null,
  }
}

/** 员工列表按名字/职位过滤（发布弹层里的小搜索框）。 */
export function filterEmployees(employees, keyword) {
  const word = String(keyword || '').trim().toLowerCase()
  if (!word) return employees
  return employees.filter((employee) => {
    const name = String(employee.name || '').toLowerCase()
    const job = String(employee.job_title || '').toLowerCase()
    return name.includes(word) || job.includes(word)
  })
}

/** 加/减若干天（日期按本地日历算，避免 `new Date('YYYY-MM-DD')` 被当 UTC 再偏
 *  一天）。传入/返回都是 `YYYY-MM-DD`；解析失败原样返回。 */
export function addDays(isoDate, days) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(isoDate || ''))
  if (!match) return isoDate
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  date.setDate(date.getDate() + Number(days || 0))
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 新任务的默认截止日：默认 18:00 收工，但 18:00 已经过了就默认明天 18:00
 *  （今天 18:00 后端判「截止时刻已经过了」，发布表单不该一打开就是个 400）。 */
export function defaultDueDate(today, deadline = DEADLINE_DEFAULT, now = new Date()) {
  if (!today) return ''
  const match = /^(\d{2}):(\d{2})$/.exec(String(deadline || ''))
  if (!match) return today
  const cutoff = new Date(`${today}T${match[1]}:${match[2]}:00`)
  return cutoff.getTime() > now.getTime() ? today : addDays(today, 1)
}

export const FREQUENCY_OPTIONS = [
  { value: 'daily', label: '每天' },
  { value: 'weekly', label: '每周几' },
  { value: 'monthly', label: '每月几号' },
]

export const WEEKDAY_OPTIONS = [
  { value: 0, label: '周一' },
  { value: 1, label: '周二' },
  { value: 2, label: '周三' },
  { value: 3, label: '周四' },
  { value: 4, label: '周五' },
  { value: 5, label: '周六' },
  { value: 6, label: '周日' },
]

/** 定期任务的频率口径：每天 / 每周一 / 每月 5 号。 */
export function frequencyText(definition) {
  if (!definition) return ''
  const kind = definition.frequency_kind
  if (kind === 'daily') return '每天'
  if (kind === 'weekly') {
    const option = WEEKDAY_OPTIONS.find((item) => item.value === Number(definition.weekday))
    return option ? `每${option.label}` : '每周'
  }
  if (kind === 'monthly') {
    return `每月 ${Number(definition.month_day) || 1} 号`
  }
  return ''
}

/** 近 N 期完成情况（前端列表用，文案与看板口径一致）。 */
export function recentPeriodsText(definition) {
  const total = Number(definition && definition.recent_total) || 0
  const passed = Number(definition && definition.recent_passed) || 0
  if (!total) return '还没有生成过期'
  return `近 ${total} 期完成 ${passed} 期`
}
