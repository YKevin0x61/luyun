/** 工作台首页（票 06）的聚合口径：**只用已有接口的响应**算出「今天」那几个数字。
 *
 *  首页不做业务动作（spec 的「首页只做分流与摘要」）：这里全是纯函数，输入是各专页
 *  已经在用的那些接口的响应，输出是页面上要摊的东西。放在 util 里而不是页面里，是为了
 *  能拿真单测钉住口径 —— 页面上写死一句 `data.items.length` 是看不出「逾期」与
 *  「待回拍」这两件事的。
 *
 *  **店长侧五个数字各自的出处**（都不新增端点）：
 *  - 今天谁上班 `onDutyNames` / `offDutyNames` ← `GET /api/scheduling/day`：同一条响应
 *    里既有上班的 `groups`，也有不在岗的 `off_people`（每人带 `leave`）。首页摊的是两半
 *    —— 谁在岗、以及谁请假 / 谁休（后者是 2026-10 店长要求加上的）。
 *  - 待批请假 `countPendingLeaves` ← `GET /api/scheduling/inbox`（店长待办那条队列）。
 *    只数 `kind === 'leave'`：换班那条对方还没点头时店长根本看不到（服务层的
 *    `pending_peer` 口径），而「还没配规则的新人」不是申请、不算待批。
 *  - 待验收 `countPendingItems` ← `GET /api/hygiene/admin/daily-queue`（不带日期时
 *    服务端已经只回「待验收」，这里照状态再筛一遍是为了历史回看那种调用也说得通）。
 *  - 逾期整改 `countOverdueFixes` ← `GET /api/hygiene/admin/fix`（服务端只回没通过的
 *    单子）。逾期 = **过了截止时间、还等着回拍**（`待回拍`）—— 那正是店长要盯的那批；
 *    已经回拍上来等验收的（`待验收`）超时与否是验收节奏的事，不算在这格里。
 *  - 人事提醒 `countHrReminders` ← `GET /api/hygiene/admin/hr-reminders`（那一页读的
 *    同一个端点）。本月该调工龄奖的人数 + 本月生日人数 + 健康证临期/过期人数，三块
 *    相加；少一块给 `null`（见那个函数上的理由）。**健康证到期 2026-10-08 从首页单独
 *    一格并进这里**（用户裁定：同一件事不再两处报数，首页那一行少一格）——它因此不再
 *    读 `daily-queue` 的 `health_cert_due`，那个字段仍留在那条已发布的 API 面上。
 *
 *  身份词表只有一处（`utils/workbenchIdentity.js`）：这里不写 'super' / 'staff'
 *  字面量，免得第 N 个消费方各写一份、改口径时漏掉。
 *
 *  **员工侧**复用「今天」页那套翻译（`utils/todayShift.js`）与工作流口径
 *  （`utils/hygieneWorkFlow.js`）：同一件事在两个页面上必须说同一句话。
 *
 *  时间来自调用方（`now`）：逾期是「现在几点」跟截止时间比出来的，取两次时间会让
 *  同一屏里的数字对不上。
 */
import { shiftText, todayHeadline, todaySubline, todayTone } from './todayShift.js'
import { STATUS_FIX_TODO, STATUS_PENDING, openRows } from './hygieneWorkFlow.js'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from './workbenchIdentity.js'
import { WASTAGE_REGISTER_PATH } from './wastagePaths.js'

/** 首页每个数字点进去的地方 —— 一处定义，模板与测试都引它，不各写一遍字面量。 */
export const HOME_LINKS = {
  /** 今天谁上班 → 排班月历（那天点开就是各班是谁）。 */
  duty: '/workbench/hr/calendar',
  /** 待批请假 → 排班待办（店长批请假的地方）。 */
  leaves: '/workbench/hr/inbox',
  /** 待验收 → 日常验收。 */
  reviews: '/workbench/floor/daily',
  /** 逾期整改 → 整改单。 */
  fixes: '/workbench/floor/fix',
  /** 人事提醒 → 人事提醒页（本月该调的工龄奖、本月生日、健康证到期都在这页）。 */
  reminders: '/workbench/hr/reminders',
  /** 我的待办条数 → 卫生待办（日常 / 专项 / 整改三个 tab 都在这儿）。 */
  myItems: '/workbench/staff/clean',
  /** 我的申请待回应 → 「今天」页那张卡。 */
  myRequests: '/workbench/staff/today',
  /** 员工档「我的班」卡下面那颗「加班」→ 加班与补钟（员工登记加班 / 补钟那一页）。 */
  myOvertime: '/workbench/staff/overtime',
  /** 员工档「我的班」卡下面那颗「损耗」→ 损耗登记（记一笔 + 看自己最近记的那几笔）。 */
  myWastage: WASTAGE_REGISTER_PATH,
}

/** 工作台身份 → 清单里的身份词。与 `utils/workbenchNav.js` 同一张表的两档词，
 *  这里只做「认不出就不渲染」这一条判据（fail-closed），不参与权限。 */
function isAdmin(identity) {
  return identity === IDENTITY_ADMIN
}

function isStaff(identity) {
  return identity === IDENTITY_STAFF
}

/** 待批请假条数：申请队列里 `kind === 'leave'` 的那几条。
 *
 *  **读不出来给 `null`**（页面画成「—」），不是 0：那一条申请队列没读到时，「没有待批」
 *  与「没读到」在店长眼里是两件事 —— 前者可以放心走人，后者得点进去看。
 */
export function countPendingLeaves(inbox) {
  if (!inbox) return null
  const requests = inbox.requests || []
  return requests.filter((row) => row && row.kind === 'leave').length
}

/** 待验收条数：没通过的那些里**交上来等着验收**的（`待验收`）。
 *
 *  判据走 `openRows`（工作流的公共口径）再筛状态，而不是写死一个字面量：不带日期调
 *  `daily-queue` 时服务端已经只回「待验收」，但历史回看那种调用会带上「待拍」——
 *  那种没拍的活不是「待验收」，不该混进这个数字。读不出来同样给 `null`。
 */
export function countPendingItems(work) {
  if (!work) return null
  const items = work.items || []
  return openRows(items).filter((row) => row && row.status === STATUS_PENDING).length
}

/** 逾期整改条数：过了 `deadline`、还停在「待回拍」的那些。
 *
 *  判不了的（没有截止时间、时间戳坏的）**不算逾期** —— 报一个猜出来的数字比不报更坏。
 *  整块读不出来（`fix` 为 `null`）给 `null`，页面画「—」。
 */
export function countOverdueFixes(fix, now = Date.now()) {
  if (!fix) return null
  const items = fix.items || []
  return items.filter((row) => {
    if (!row || row.status !== STATUS_FIX_TODO || !row.deadline) return false
    const stamp = Date.parse(row.deadline)
    return Number.isFinite(stamp) && stamp <= now
  }).length
}

/** 店长首页「等我处理」：三类待办各取几条，拼成可点的行（2026-10-18 实用性改进）。
 *
 *  为什么要有它：四个读数只回答「有几件」，不回答「是哪几件」—— 店长得挨个点进去才
 *  知道今天具体要办什么。数据全部来自首页**已经在拉**的三份响应（不新增接口），
 *  一条一类、按「越急越靠前」排：逾期整改 → 待验收 → 待批请假。
 *
 *  口径与三个专页一致（只数店长真能动手的那些）：验收只取「待验收」、请假只取
 *  `kind === 'leave'`（换班要对方点头才进待办）、整改只取「待回拍且已过 deadline」。
 *  `null`（那块读不出来）不编造条目 —— 少一类就少一类，别把「没读到」说成「没有」。
 */
/** 首页只展示前 6 条（一屏摘要）；**总数**由调用方拿去写「还有 N 件」。 */
export const MANAGER_TODO_LIMIT = 6
export function managerTodoRows({ inbox = null, queue = null, fix = null, now = Date.now() } = {}) {
  const rows = []
  for (const row of (fix && fix.items) || []) {
    if (!row || row.status !== STATUS_FIX_TODO || !row.deadline) continue
    const stamp = Date.parse(row.deadline)
    if (!Number.isFinite(stamp) || stamp > now) continue
    rows.push({
      key: `fix-${row.id}`,
      kind: '整改',
      title: [row.zone_name, row.ticket_type].filter(Boolean).join(' · ') || '整改单',
      meta: (row.body_text || '').trim().slice(0, 24) || '已过整改时限',
      to: '/workbench/floor/fix',
      tone: 'bad',
    })
  }
  for (const row of (queue && queue.items) || []) {
    if (!row || row.status !== STATUS_PENDING) continue
    rows.push({
      key: `daily-${row.item_id}-${row.shift}`,
      kind: '验收',
      title: [row.zone_name, row.item_name].filter(Boolean).join(' · ') || '日常验收',
      meta: [row.shift, row.submitter_phone].filter(Boolean).join(' · ') || '待验收',
      to: '/workbench/floor/daily',
      tone: 'warn',
    })
  }
  for (const req of (inbox && inbox.requests) || []) {
    if (!req || req.kind !== 'leave') continue
    const span = req.end_date && req.end_date !== req.start_date ? `${req.start_date} ~ ${req.end_date}` : req.start_date
    rows.push({
      key: `leave-${req.id}`,
      kind: '请假',
      title: `${req.employee_name || '（没有名字）'} · ${span || ''}`.trim(),
      meta: (req.days || []).length ? `${req.days.length} 天 · 待批` : '待批',
      to: '/workbench/hr/inbox',
      tone: 'warn',
    })
  }
  return rows
}

/** 人事提醒条数：本月**该调工龄奖**的人数（含历史欠调）＋ 本月**生日**人数 ＋
 *  **健康证临期/过期**人数。
 *
 *  三块都要在才给总数：少一块时相加出来的数会偏小、看着像「这个月没事」，而店长
 *  正是照这个数决定要不要点进去 —— 宁可画「—」（同 `staffWorkCounts` 对三块待办
 *  的处理）。`incomplete`（档案待补）**不算进来**：它是提醒页里的一块，不是首页
 *  这一格的语义（这一格回答的是「本月有几件人事的事要办」——而「算不出来」不是
 *  一件要办的事，是档案缺项）。
 *
 *  `over`（档案里的工龄奖高于应为）也不计入 —— 那类只标出来、不需要人动手。
 *
 *  健康证那条（2026-10-08）：它原来是首页的独立一格，读 `daily-queue` 的
 *  `health_cert_due`；用户裁定并进这里，于是首页那一行少一格、同一件事不再两处
 *  报数。判据仍在服务端（`profile.health_cert_status`），前端只加总。
 */
export function countHrReminders(reminders) {
  if (!reminders) return null
  const blocks = [reminders.seniority, reminders.birthdays, reminders.certs]
  const counts = blocks.map((block) => (block ? block.count : undefined))
  if (counts.some((count) => count === undefined || count === null)) return null
  const sum = counts.reduce((total, count) => total + Number(count), 0)
  return Number.isFinite(sum) ? sum : null
}

/** 今天谁上班：按班次分组，带人数与名字（休的人不在这份里 —— 服务端给了 `off_people`）。
 *
 *  `count` 数的是**有名字的人**：服务端的 `count` 与 `people` 是同一批人，名字为空的
 *  （花名册上刚被清掉）在页面上渲染不出来，人数却算进去的话，店长会看到「白班 2 人」
 *  而名字只有一个 —— 人数与名单对不上比人数少一个更难查。
 */
export function onDutyNames(day) {
  const groups = (day && day.groups) || []
  return groups.map((group) => {
    const people = (group && group.people) || []
    const names = people.map((person) => String((person && person.name) || '').trim()).filter(Boolean)
    return {
      shift: (group && group.shift && group.shift.name) || '',
      count: names.length,
      names,
    }
  })
}

/** 今天不在岗的人：**请假**与**休**两组，各自带人数与名字 —— 「今天谁上班」那一格给出的
 *  自然还有它的补集（2026-10 店长要求：休假名单也要摊在首页，请假的人算在里面）。
 *
 *  两组不能合成一份：`off_people[].leave` 是服务端给的判据（`true` = 批过假的请假、
 *  `false` = 本来就休），店长对这两件事的动作不一样（请假那条要记着、要管家里的排班；
 *  本来就休的不用管）。所以**请假排前面**，用户一眼看到的是真正有事的那批。
 *
 *  `count` 与 `onDutyNames` 同口径：只数有名字的人（名字渲染不出来时，人数与名单对不上
 *  比人数少一个更难查）。没人不在岗时给空数组，页面据此整块不渲染。
 */
export function offDutyNames(day) {
  const people = (day && day.off_people) || []
  const buckets = { leave: [], rest: [] }
  for (const person of people) {
    const name = String((person && person.name) || '').trim()
    if (!name) continue
    buckets[person && person.leave ? 'leave' : 'rest'].push(name)
  }
  return [
    { label: '请假', tone: 'leave', names: buckets.leave },
    { label: '休', tone: 'rest', names: buckets.rest },
  ]
    .filter((group) => group.names.length)
    .map((group) => ({ ...group, count: group.names.length }))
}

/** 员工那一档的「我的班次」：三行字与语气都复用「今天」页那套（`utils/todayShift.js`）。
 *
 *  「休」「请假」「还没排」「班次已调整」四态必须分得开 —— 从工作台首页与从「今天」页
 *  看到同一句话，才不会有人以为今天不用上班。
 */
export function staffShiftCell(day) {
  return {
    headline: todayHeadline(day),
    subline: todaySubline(day),
    tone: todayTone(day),
  }
}

/** 「往后几天」那一行：用「今天」页同一套 `shiftText`（休 / 请假 / 已调整分得开）。 */
export function staffNextDays(me) {
  const days = (me && me.days) || []
  return days.slice(1, 4).map((day) => ({
    business_date: day.business_date,
    text: shiftText(day),
  }))
}

/** 员工这一档的待办分块：日常 / 专项 / 整改，三块各自数没通过的那些。
 *
 *  与员工端「卫生待办」页的三个角标同一口径（`openRows`）。**有一块读不出来整格给
 *  `null`**（页面画「—」）：三块相加的总数里少了一块，看起来就像「只剩这么多活」，
 *  而员工会照这个数字收工。要总分就得三块都在。
 */
export function staffWorkCounts({ daily, deep, fix, requests: inbox } = {}) {
  const count = (work) => (work ? openRows(work.items || []).length : null)
  const buckets = { daily: count(daily), deep: count(deep), fix: count(fix) }
  const missing = Object.values(buckets).some((value) => value === null)
  return {
    ...buckets,
    pending: missing
      ? null
      : buckets.daily + buckets.deep + buckets.fix,
    // 等我回应的换班（`incoming`）：那些在「今天」页那张卡上处理，不在卫生待办里。
    incoming: inbox ? ((inbox.incoming || []).length) : null,
  }
}

/**
 * 首页要渲染的东西，一次算清。
 *
 * @param {{
 *   identity?: 'super'|'staff'|null,
 *   day?: object|null,      // GET /api/scheduling/day（店长：今天谁上班）
 *   inbox?: object|null,    // GET /api/scheduling/inbox（店长：待批请假；员工：我的申请）
 *   queue?: object|null,    // GET /api/hygiene/admin/daily-queue（店长：待验收）
 *   fix?: object|null,      // GET /api/hygiene/admin/fix（店长：逾期整改；员工：我的整改）
 *   reminders?: object|null,// GET /api/hygiene/admin/hr-reminders（店长：人事提醒）
 *   deep?: object|null,     // GET /api/hygiene/staff/deep-clean（员工：我的专项）
 *   daily?: object|null,    // GET /api/hygiene/staff/daily-work（员工：我的日常）
 *   me?: object|null,       // GET /api/scheduling/me（员工：我的班次 / 工作区）
 *   now?: number,
 * }} input
 * @returns {{
 *   view: 'super'|'staff'|null,
 *   duty: {total: number, groups: Array<{shift: string, count: number, names: string[]}>,
 *          off: Array<{label: string, tone: string, count: number, names: string[]}>}|null,
 *   leaves: number|null, reviews: number|null, fixes: number|null, reminders: number|null,
 *   me: {shift: {headline: string, subline: string, tone: string}, next: Array<object>}|null,
 *   work: {daily: number, deep: number, fix: number, pending: number, incoming: number}|null,
 *   incoming: number|null,
 * }}
 *   `view` 为 null 表示**还没探出身份**：调用方据此一个数字都不渲染 —— 别先闪一个
 *   可能不对的视角（探针回来之前谁也不知道这是谁）。
 */
export function workbenchHomeSummary({
  identity = null,
  day = null,
  inbox = null,
  queue = null,
  fix = null,
  reminders = null,
  deep = null,
  daily = null,
  me = null,
  now = Date.now(),
} = {}) {
  if (isAdmin(identity)) {
    const groups = onDutyNames(day)
    return {
      view: IDENTITY_ADMIN,
      duty: {
        total: (day && typeof day.total === 'number') ? day.total : groups.reduce((sum, group) => sum + group.count, 0),
        groups,
        // 不在岗的（请假 / 休）：跟上班名单同一份响应里的 `off_people`。
        off: offDutyNames(day),
      },
      leaves: countPendingLeaves(inbox),
      reviews: countPendingItems(queue),
      fixes: countOverdueFixes(fix, now),
      reminders: countHrReminders(reminders),
      // 四个数字的「展开版」：具体哪几件事（按急迫排，最多 6 条）。
      todos: managerTodoRows({ inbox, queue, fix, now }),
      me: null,
      work: null,
      incoming: null,
    }
  }

  if (isStaff(identity)) {
    const today = ((me && me.days) || []).find((row) => row && row.is_today) || ((me && me.days) || [])[0] || null
    const work = staffWorkCounts({
      daily: daily || queue,
      deep,
      fix,
      requests: inbox,
    })
    return {
      view: IDENTITY_STAFF,
      duty: null,
      leaves: null,
      reviews: null,
      fixes: null,
      reminders: null,
      me: { shift: staffShiftCell(today), next: staffNextDays(me) },
      work,
      incoming: work.incoming,
      todos: null,
    }
  }

  return {
    view: null,
    duty: null,
    leaves: null,
    reviews: null,
    fixes: null,
    reminders: null,
    todos: null,
    me: null,
    work: null,
    incoming: null,
  }
}
