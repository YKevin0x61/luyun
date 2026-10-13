/** 「那一天」能做什么（票 15：请假与换班融进排班月历）的人话翻译层。
 *
 * 月历点开某一天，弹层里除了名单还要给出两件事：**这天我的假怎么请、这天的班跟谁换**。
 * 能不能做、不能做时说什么，全是判据 —— 放到 util 里才有真单测（页面模板的 grep
 * 断言抓不住「过去的日子被写成了还没排」）。
 *
 * 三条口径跟服务端对齐（`services/scheduling/store.py` 的 `submit_leave` / `submit_swap`）：
 *   · 过去的日期两边都拒（`past_leave` / `past_swap`）—— 这里先说，别让人拨完日期才被打回；
 *   · 展开窗口（`window_end`）之后的日期两边都拒（那天本来就没排班可改）；
 *   · 换班**不要求那天有班次**，只要求那天有那一行（`_day_shift` 的 `scheduled`）——
 *     休的那天也换得（批了就是对方接走你那天、你那天没班），所以判据是 `scheduled`
 *     而不是「有没有班次」。请假反过来：已经批了假的那天不用再请。
 */

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

/** 那天能不能提请假。已经批了假的那天不必再请（再提一条只会多一张一样的卡给店长）。 */
export function leaveAction(day, ctx = {}) {
  const blocked = sharedBlock(day, ctx)
  if (blocked) return verdict(blocked)
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
  if (day.leave) return verdict('这天你请假，不用换班')
  if (!day.scheduled) return verdict('这天还没排到你，没法换')
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
 *  `groups` 是 `/api/scheduling/me/day` 的 `groups`（空组服务端已经滤掉）。 */
export function swapCandidates(groups, employeeId) {
  const out = []
  for (const group of groups || []) {
    for (const person of (group && group.people) || []) {
      if (!person) continue
      if (employeeId != null && Number(person.id) === Number(employeeId)) continue
      out.push({
        id: person.id,
        name: person.name,
        zone: person.zone || '',
        shift_name: (group && group.shift_name) || '',
      })
    }
  }
  return out
}

/** 候选行上的第二行小字：「白班 · 案板」。两个都能缺，缺了就不占那一格。 */
export function candidateLine(person) {
  if (!person) return ''
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
