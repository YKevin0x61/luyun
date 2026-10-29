/** 「那一天」能做什么（票 15：请假与换班融进排班月历）的人话翻译层。
 *
 * 月历点开某一天，弹层里除了名单还要给出两件事：**这天我的假怎么请、这天的班跟谁换**。
 * 能不能做、不能做时说什么，全是判据 —— 放到 util 里才有真单测（页面模板的 grep
 * 断言抓不住「过去的日子被写成了还没排」）。
 *
 * 口径跟服务端对齐（`services/scheduling/store.py` 的 `submit_leave` / `submit_swap`）：
 *   · 过去的日期两边都拒（`past_leave` / `past_swap`）—— 这里先说，别让人拨完日期才被打回；
 *   · 展开窗口（`window_end`）之后的日期两边都拒（那天本来就没排班可改）；
 *   · 换班**不要求那天有班次**，只要求那天有那一行（`_day_shift` 的 `scheduled`）——
 *     休的那天也换得（批了就是对方接走你那天、你那天没班），所以判据是 `scheduled`
 *     而不是「有没有班次」。请假反过来：已经批了假的那天不用再请。
 *   · **22:00 之后不能再提次日**（2026-10-29 用户裁定，`next_day_locked` 由服务端下发）；
 *   · **2 天以上的假要提前 7 天**（`leave_lead_days`，判据在 `leaveRangeAction`）。
 */
import { shiftDate } from './schedulingWeekPick.js'
import { dayDiff } from './dateRange.js'

/** 弹层里现在看的是哪一屏。`day` 是那份名单；另两屏是两张表单。 */
export const DAY_VIEW = { day: 'day', leave: 'leave', swap: 'swap' }

/** 某件事能不能做：`{ ok, reason }`。`ok=false` 时 `reason` 是一句给员工看的话。 */
function verdict(reason) {
  return reason ? { ok: false, reason } : { ok: true, reason: '' }
}

/** 过去 / 窗口外这两条两边共用（唯一的区别是各自那句文案）。 */
function sharedBlock(day, ctx) {
  const date = day && day.business_date
  if (!date) return '这天读不出来'
  const today = ctx && ctx.today
  // 字符串比较就够：ISO 日期（YYYY-MM-DD）的字典序就是时间序。
  if (today && date < today) return '这天已经过去了'
  const windowEnd = ctx && ctx.windowEnd
  if (windowEnd && date > windowEnd) return '排班还没铺到这天'
  return ''
}

/** 这一格是不是「次日」、而且已经过了 22:00 的截止（2026-10-29 用户裁定）。
 *
 *  `ctx.nextDayLocked` 由服务端算（店长那台机器的时间才是准的），这里只负责「哪天是明天」
 *  —— 用营业日加一天，不拿手机上的今天加一天。
 */
function nextDayLocked(day, ctx) {
  const date = day && day.business_date
  const today = ctx && ctx.today
  if (!date || !today || !ctx.nextDayLocked) return false
  return date === shiftDate(today, 1)
}

/** 那天能不能提请假。已经批了假的那天不必再请（再提一条只会多一张一样的卡给店长）。 */
export function leaveAction(day, ctx = {}) {
  const blocked = sharedBlock(day, ctx)
  if (blocked) return verdict(blocked)
  // 22:00 之后明天的假提不了：店长那会儿已经把人排定，批下来等于半夜改明天的班。
  if (nextDayLocked(day, ctx)) return verdict('晚上 10 点过了，明天的假提不了')
  if (day.leave) return verdict('这天已经是请假')
  return verdict('')
}

/** 那天能不能提换班。
 *
 *  比请假多一条：那天得有你的那一行（`scheduled`）。**休的那天也算有** —— 服务端
 *  `_day_shift` 只问行在不在，休的那天换班是把对方那天的班接过来，是合法的换法。
 *  「这天还没排到你」（没有那一行）跟「这天你休」在数据上就是两个字段，别混成一句。
 */
export function swapAction(day, ctx = {}) {
  const blocked = sharedBlock(day, ctx)
  if (blocked) return verdict(blocked)
  if (nextDayLocked(day, ctx)) return verdict('晚上 10 点过了，明天的班换不了')
  if (day.leave) return verdict('这天你请假，不用换班')
  if (!day.scheduled) return verdict('这天还没排到你，没法换')
  return verdict('')
}

/** 一段日期的假能不能提：**2 天以上要提前 7 天**（2026-10-29 用户裁定）。
 *
 *  天数与提前量都跟服务端同一套（`MULTI_DAY_LEAVE_LEAD_DAYS` 由 `/me/month` 下发），
 *  这里只是提前把那句话说出来，别让人填完事由才被打回。单日的假不看这一条。
 */
export function leaveRangeAction(start, end, ctx = {}) {
  if (!start) return verdict('先选开始那天')
  const last = end && end >= start ? end : start
  const days = dayDiff(start, last)
  const lead = Number((ctx && ctx.leaveLeadDays) || 0)
  const today = ctx && ctx.today
  if (days >= 2 && lead > 0 && today) {
    const earliest = shiftDate(today, lead)
    if (start < earliest) {
      return verdict(`请 ${days} 天的假要提前 ${lead} 天：最早从 ${earliest} 起请`)
    }
  }
  return verdict('')
}

/** 两个动作一起给（模板里一次读完，不在模板里写判据）。 */
export function dayActions(day, ctx = {}) {
  return { leave: leaveAction(day, ctx), swap: swapAction(day, ctx) }
}

/** 两个动作都做不了时那一行小字：两个理由一样就只说一遍。
 *
 *  过去的日子两件事都不能办，各写一句「这天已经过去了」是最常见的一屏 —— 说两遍
 *  同一句话只会让人以为是两条不同的限制。 */
export function actionsNote(acts) {
  const leave = (acts && acts.leave) || { ok: true, reason: '' }
  const swap = (acts && acts.swap) || { ok: true, reason: '' }
  if (leave.ok && swap.ok) return ''
  if (!leave.ok && !swap.ok && leave.reason === swap.reason) return leave.reason
  return [
    !leave.ok ? `请假：${leave.reason}` : '',
    !swap.ok ? `换班：${swap.reason}` : '',
  ].filter(Boolean).join(' · ')
}

/** 那天有没有我的申请（区间两端都算；`end_date` 空就是单日）。
 *
 *  单日申请与跨天申请走同一条：员工要的是「我点开的这一天有没有事」，
 *  一条请了 9/20–9/22 的假，在这三天里点开都该看得见。 */
export function requestsOnDay(requests, businessDate) {
  if (!businessDate) return []
  return (requests || []).filter((request) => {
    if (!request || !request.start_date) return false
    const last = request.end_date || request.start_date
    return businessDate >= request.start_date && businessDate <= last
  })
}

/** 那天有没有人找我换班（票 09 的那几条待回应，服务端只给还没回应的）。 */
export function incomingOnDay(incoming, businessDate) {
  if (!businessDate) return []
  return (incoming || []).filter((card) => card && card.business_date === businessDate)
}

/** 那天能跟谁换：**那天在班的人**（去掉我自己），一个班次一组摊平。
 *
 *  这是「融入月历」最要紧的一步 —— 员工在月历上点开某天，看见的是那天在班的人，
 *  直接点一个人就能换；`/me/colleagues`（全店名单）是兜底：那天没班的人也在服务端
 *  允许的范围内（他那天空着，批了就是他接走你的班），所以名单之外还留一条路。
 *
 *  2026-10-29 用户裁定：**名单里那天「休」的人也算候选人**（`offPeople`），不用再翻到
 *  全店名单里去找。休的那位跟「那天没班」是同一件事的服务端口径（`submit_swap` 只要求
 *  提的人那天手上真有班），批了就是他接走你的班。
 *
 *  **「请假」那位不进名单**：那不是空班，是批过的假 —— 换班批准会把那条假盖掉
 *  （服务端只校验申请人自己的班，挡不住这一边），所以在挑人的时候就不给。
 *
 *  `groups` / `offPeople` 都是 `/api/scheduling/me/day` 的（空组服务端已经滤掉）。 */
export function swapCandidates(groups, employeeId, offPeople = []) {
  const out = []
  const isMe = (id) => employeeId != null && Number(id) === Number(employeeId)
  for (const group of groups || []) {
    for (const person of (group && group.people) || []) {
      if (!person) continue
      if (isMe(person.id)) continue
      out.push({
        id: person.id,
        name: person.name,
        zone: person.zone || '',
        shift_name: (group && group.shift_name) || '',
      })
    }
  }
  for (const person of offPeople || []) {
    if (!person || person.leave) continue
    if (isMe(person.id)) continue
    out.push({
      id: person.id,
      name: person.name,
      zone: '',
      shift_name: '',
      // 这一位那天休：候选行与预览都靠这个标记说话，别拿空的 shift_name 去猜。
      off: true,
    })
  }
  return out
}

/** 候选行上的第二行小字：「白班 · 案板」。两个都能缺，缺了就不占那一格。
 *  那天休的人在 `shift_name` 上本来就是空的，这里给它一句「那天休」。 */
export function candidateLine(person) {
  if (!person) return ''
  if (person.off) return '那天休'
  return [person.shift_name, person.zone].filter(Boolean).join(' · ')
}

/** 选中一位同事之后那一句「批了会怎样」（跟店长待办那张卡同一个道理，说法更短）：
 *  两个人班次不同 → 对调；**两个人那天同一个班次** → 换的是岗位（工作区）；
 *  他那天没班 → 他接你的班；我那天休 → 我接他的班。
 *
 *  同班次那一档必须单独说：写「批了你上白班、他上白班」是句让人发懵的废话 ——
 *  店里的白班分几个区（案板 / 明档 / 蒸柜），批了是对调工作区，不是对调班次。
 */
export function swapPreview(me, peer) {
  const mine = me && me.shift_name
  const theirs = peer && peer.shift_name
  const who = (peer && peer.name) || '对方'
  if (mine && theirs && mine === theirs) {
    return `你俩那天都是${mine}：批了换的是岗位（工作区）`
  }
  if (mine && theirs) return `批了你上${theirs}、${who}上${mine}`
  // 休（`off`）与「那天没班」在服务端是同一档，但员工眼里是两句话：前者是他本来就休，
  // 后者是这天还没铺到他 —— 2026-10-29 起休的人也能当候选人，这句得说准。
  if (mine && peer && peer.off) return `${who}那天休：批了 ${who} 接你的${mine}，你那天没班`
  if (mine && !theirs) return `他那天本来没班：批了 ${who} 接你的${mine}`
  if (!mine && theirs) return `你那天休：批了你去上${theirs}`
  return '两个人那天都没有班：批了也不改什么'
}

/** 「从今天页跳过来」那两参数：`?act=leave|swap`。坏值一律当没给（手改地址栏、
 *  老书签里可能什么都有），不在页面里做这件事是因为它也要被测。 */
export function actFromQuery(query) {
  const act = String((query && query.act) || '')
  return act === DAY_VIEW.leave || act === DAY_VIEW.swap ? act : ''
}

/** `?date=YYYY-MM-DD`：只认这个形状（多一个空格、少一个 0 都当没给）。 */
export function dateFromQuery(query) {
  const date = String((query && query.date) || '')
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : ''
}

/** 日期 → 它所在的月份（`2026-10-05` → `2026-10`）。翻月用的是同一个字符串前缀。 */
export function monthOfDate(date) {
  return /^\d{4}-\d{2}/.test(String(date || '')) ? String(date).slice(0, 7) : ''
}
