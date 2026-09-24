/** 请假 / 换班申请（票 08、09）的人话翻译层。
 *
 * 服务端给的是机器可读的 `status` / `kind` 加一串日期（`GET|POST|DELETE
 * /api/scheduling/me/requests`、票 09 的 `/me/colleagues` 与 `/me/swaps*`、店长那三条
 * `/api/scheduling/inbox*`）：这层只负责翻成人话，页面里不再各写一份。**放在 util 里
 * 而不是页面里**是为了能用真单测钉住状态表 —— 页面模板的 grep 断言抓不住
 * 「rejected 被写成了等店长批」。
 */

import { dayLabel } from './todayShift'

// 状态机（`.scratch/scheduling/spec.md`）：请假只走「等店长批」这一跳，
// 换班（票 09）多一步「等对方确认」（`pending_peer`），所以只认这四个 + 撤回。
export const REQUEST_STATUS = {
  pendingPeer: 'pending_peer',
  pendingManager: 'pending_manager',
  approved: 'approved',
  rejected: 'rejected',
  cancelled: 'cancelled',
}

const STATUS_TEXT = {
  pending_peer: '等对方同意',
  pending_manager: '等店长批',
  approved: '批了',
  rejected: '驳回了',
  cancelled: '已撤回',
}

const STATUS_TONE = {
  pending_peer: 'wait',
  pending_manager: 'wait',
  approved: 'ok',
  rejected: 'no',
  cancelled: 'off',
}

/** 状态 → 人话。认不出的状态原样回：服务端加了新状态也不至于显示一片空白。 */
export function statusText(status) {
  return STATUS_TEXT[status] || status || ''
}

/** 状态 → 语气（页面上色用）。 */
export function statusTone(status) {
  return STATUS_TONE[status] || 'off'
}

/** 还能撤回吗：等对方与等店长这两种都撤得回（批完/驳完/撤过的都撤不动）。 */
export function canCancel(request) {
  return (
    !!request &&
    (request.status === REQUEST_STATUS.pendingManager ||
      request.status === REQUEST_STATUS.pendingPeer)
  )
}

/** 申请的种类。换班是票 09 的，这里先把词备好：同一张表靠 `kind` 分开。 */
export function kindText(kind) {
  if (kind === 'leave') return '请假'
  if (kind === 'swap') return '换班'
  return kind || ''
}

/** 事由那一行：没写就不占一行（空事由服务端存的是 null）。 */
export function noteText(note) {
  const text = String(note || '').trim()
  return text ? `事由：${text}` : ''
}

/** 「9/25」——卡片上的日期与区间用它（周几只有明细里才有意义）。 */
export function shortDay(businessDate) {
  const [, month, day] = String(businessDate || '').split('-').map(Number)
  return month && day ? `${month}/${day}` : ''
}

/** 申请区间：一天只说一次，跨天用「–」连起来。 */
export function requestRangeText(startDate, endDate) {
  const first = shortDay(startDate)
  const last = shortDay(endDate || startDate)
  if (!first) return ''
  return !last || last === first ? first : `${first} – ${last}`
}

/** 员工自己那条申请的时间线：提的哪天 + 现在到哪一步。
 *
 *  `dayLabel`（`utils/todayShift.js`）给的是「9/25 周五」：员工要拿它跟自己的班对，
 *  周几有用。
 */
export function requestLine(request) {
  if (!request) return ''
  const when = request.start_date === request.end_date || !request.end_date
    ? dayLabel(request.start_date)
    : `${dayLabel(request.start_date)} 起 ${dayLabel(request.end_date)}`
  if (request.kind === 'swap') return `换班 · ${when} · ${swapStatusText(request)}`
  return `${kindText(request.kind)} · ${when} · ${statusText(request.status)}`
}

/** 换班那条现在卡在谁那里（票 09 的验收 7）——「等对方」必须点名，光说「等对方同意」不够。
 *
 *  `peer_name` 是服务端给的（`my_requests` 会补上）；拿不到名字时说「对方」，
 *  不编一个名字出来。
 */
export function swapStatusText(request) {
  const who = (request && request.peer_name) || '对方'
  switch (request && request.status) {
    case REQUEST_STATUS.pendingPeer:
      return `等 ${who} 同意`
    case REQUEST_STATUS.pendingManager:
      return `${who} 同意了 · 等店长批`
    case REQUEST_STATUS.approved:
      return '换成了'
    case REQUEST_STATUS.rejected:
      return `${who} 没同意`
    case REQUEST_STATUS.cancelled:
      return '已撤回'
    default:
      return statusText(request && request.status)
  }
}

/** 别人问我换班的那张卡（票 09 的验收 2）：谁、哪天、他那天什么班、我那天什么班。
 *
 *  先说「他那天」，再说「我那天」：员工要先看懂对方拿什么来换，才谈得上同意不同意。
 */
export function incomingLine(card) {
  if (!card) return ''
  const when = dayLabel(card.business_date)
  const theirs = card.their_scheduled ? card.their_shift_name || '班次已调整' : '那天没班'
  const mine = card.my_scheduled ? `你那天 ${card.my_shift_name || '班次已调整'}` : '你那天休'
  return `${card.employee_name || '同事'} 想跟你换 ${when} · 他那天 ${theirs} · ${mine}`
}

/** 店长待办里换班卡的那一句「批了会怎样」：两个人那天的班对调，谁上哪个班说清楚。 */
export function swapPreviewLine(day, applicant, peer) {
  if (!day) return ''
  const when = shortDay(day.business_date)
  if (day.past) return `${when} 已经过去：批了也不改历史`
  const mine = day.current_shift_name || ''
  const theirs = day.peer_scheduled ? day.peer_shift_name || '班次已调整' : ''
  const one = applicant || '申请人'
  const two = peer || '对方'
  if (mine && theirs) return `${when} ${mine} ⇄ ${theirs}：批了 ${one} 上${theirs}、${two} 上${mine}`
  if (mine && !theirs) return `${when} ${two} 那天休：批了 ${two} 接${mine}，${one} 那天没班`
  if (!mine && theirs) return `${when} ${one} 那天没班：批了 ${one} 接${theirs}，${two} 那天没班`
  return `${when} 两个人那天都没有班：批了也不改什么`
}

/** 批完换班给店长的一句回执（跟请假的 `approveReceipt` 同一个道理）。
 *
 *  「批了」不等于「改了」：批准一条已经过去的换班，服务层照样记成已批准，
 *  但两个人的排班一个字不动。
 */
export function swapApproveReceipt(result) {
  const applied = ((result && result.applied_days) || []).map(shortDay).join('、')
  const skipped = ((result && result.skipped_days) || []).map(shortDay).join('、')
  if (applied && !skipped) return `批了：${applied} 两个人的班对调了`
  if (applied && skipped) return `批了：${applied} 对调了；${skipped} 已经过去，排班没动`
  return `批了，但这天已经过去：排班没动${skipped ? `（${skipped}）` : ''}`
}

/** 待办卡片上那一行「批了会怎样」（票 08 的验收 2）：一天一句。
 *
 *  三件事分得开（跟 `todayShift.js` 那张表同一个道理）：
 *    已经过去的日子   → 批了也不动（票 07 的「过去不改」）
 *    那天没你的班     → 这个人那天没有排班行（可能压根还没配规则）
 *    正常             → 「原定白班 · 批了之后 白班剩 1 人」
 *
 *  `after` 只列服务端挑出来的班次（那天本来有人的班 + 申请人自己那个班），
 *  所以这里不必再过滤「0 人」的格子。**没你的班也要报人头数** —— 人头是按别人算的，
 *  没配规则的新人（票 08 验收 7 点名的那拨）照样该看见「批了之后还剩几个人」。
 */
export function previewLine(day) {
  if (!day) return ''
  const when = shortDay(day.business_date)
  if (day.past) return `${when} 已经过去：批了也不改历史`
  const left = (day.after || [])
    .map((item) => `${item.shift_name}剩 ${item.count} 人`)
    .join(' · ')
  if (!day.scheduled) {
    const mine = `${when} 那天没有你的班`
    return left ? `${mine} · 批了之后 ${left}` : mine
  }
  const current = day.current_shift_name ? `原定${day.current_shift_name}` : '那天本来就休'
  return left ? `${when} ${current} · 批了之后 ${left}` : `${when} ${current}`
}

/** 批完给店长的一句回执：批到了哪几天、哪几天因为已经过去而没动。
 *
 *  「批了」不等于「改了」：批准一条昨天的请假，服务层照样记成已批准，但排班一个字
 *  不动（票 07 的「过去不改」）。回执必须把这两件事分开说 —— 一句笼统的「已批准」
 *  会让店长以为那天的人手变了。
 */
export function approveReceipt(result) {
  const applied = ((result && result.applied_days) || []).map(shortDay).join('、')
  const skipped = ((result && result.skipped_days) || []).map(shortDay).join('、')
  if (applied && !skipped) return `批了：${applied} 记成请假`
  if (applied && skipped) return `批了：${applied} 记成请假；${skipped} 已经过去，排班没动`
  return `批了，但这几天都已经过去：排班没动${skipped ? `（${skipped}）` : ''}`
}

/** 把「还没配规则的人」分成两拨：要提醒的与不提醒的。
 *
 *  口径与排班页底下那根条一致（`SchedulingCalendarView.vue` 的 `needsRule`：在职 =
 *  批准过、没停用）。停用的人不该天天挂在待办上；没批准的人也登不进员工端、提不了假。
 *  两拨都返回，页面自己决定说不说 —— 服务层把 `approved` / `disabled` 一起给出来，
 *  就是不替前端下这个结论。
 */
export function splitRuleless(people) {
  const active = []
  const muted = []
  for (const person of people || []) {
    if (person && person.approved && !person.disabled) active.push(person)
    else muted.push(person)
  }
  return { active, muted }
}
