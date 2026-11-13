import { computed, ref } from 'vue'
import { api } from '../api/client'
import { eachDayInRange } from '../utils/dateRange'
import {
  PICK_HALF_REST,
  PICK_MAX_CELLS,
  PICK_REST,
  PICK_RESTORE,
  PICK_SHIFT,
  PICK_SKIP_TEXT,
  PICK_VOID,
  batchPayload,
  normalizeResult,
  overLimit,
  pastDatesIn,
  pickPending,
  predictSkips,
  snapshotCells,
  undoPlan,
} from '../utils/schedulingWeekPick'
import { mergeShiftList } from '../utils/shiftTable'

/**
 * 排班 board（ADR 0142）：月历页的四个投影、全部读请求与**写口编排**。
 *
 * 零参数、**不自动拉取**：页面从 URL 读出 month / view / weekStart 后显式 `init()`，
 * 再按既有顺序触发首个 loader。URL 归页面（这里不 import router、不读 query），
 * nudge 订阅也不在这里 —— 页面把 `refreshVisible` 接到自己的 pull 回调上。
 *
 * 写口（落笔编排、单日覆盖）已收进本文件；规则（PUT/DELETE `/rules`、固定区）仍归页面。
 * 页面的笔、选区、编辑表单、弹窗与模板一个字不动 —— 这一层只做「这一笔怎么写、写完说什么」。
 */
export function useSchedulingBoard() {
  // ── 读状态：页面不再各自声明 ─────────────────────────────────────────────
  const loading = ref(true)
  const errorText = ref('')
  const monthValue = ref('')
  const calendar = ref(null)
  const selectedDate = ref('')
  const dayDetail = ref(null)
  const shifts = ref([])
  const week = ref(null)
  const weekStart = ref('')
  const weekError = ref('')
  const roster = ref([])
  const zones = ref([])
  const maxCycleDays = ref(null)
  const pendingCount = ref(null)
  const pendingMarks = ref({})
  const inboxRequests = ref([])
  const leaveBalance = ref({}) // employeeId → 天数（负数是欠班）
  const leaveMonth = ref('') // 这份数字属于哪个月（'YYYY-MM'），只进 title
  const view = ref('month') // 'month' | 'week' | 'roster'

  /** 月历请求的序号：慢网下只认最后一次请求的响应。 */
  let calendarSeq = 0

  function currentMonthValue() {
    const now = new Date()
    const month = String(now.getMonth() + 1).padStart(2, '0')
    return `${now.getFullYear()}-${month}`
  }

  /** 显式初始化：只落值，不发请求（首屏由页面按 calendar → panel → notes 的顺序触发）。 */
  function init({ month = '', view: initialView = 'month', weekStart: initialStart = '' } = {}) {
    monthValue.value = month || ''
    view.value = initialView || 'month'
    weekStart.value = initialStart || ''
  }

  /** 只改视角；写 URL / 清选区仍归页面。 */
  function setView(next) {
    view.value = next
  }

  // ── 月历 + 当天 ──────────────────────────────────────────────────────────
  async function loadMonth(month, silent = false) {
    errorText.value = ''
    const value = month || monthValue.value || currentMonthValue()
    // 慢网下连点「上月」（或「上月」与「本月」并发）时，先到的旧响应会把后点的那个月
    // 覆盖掉。只有最后一次请求的响应算数。
    const seq = (calendarSeq += 1)
    // 月份**立刻**更新：原来只在成功之后才写，于是连点两次算出来的目标是同一个月，
    // 第二次点击看起来像没反应。
    monthValue.value = value
    // `silent`：实时 nudge 触发的重读不翻 loading —— 每来一条就闪一下白，很吵。
    if (!silent) loading.value = true
    try {
      const data = await api.get('/api/scheduling/calendar', { month: value })
      if (seq !== calendarSeq) return
      calendar.value = data
      shifts.value = data.shifts || []
      monthValue.value = data.month
      // 选中那天：今天在这个月就选今天，否则选 1 号（原型 B 一进来就是「今天」那格）。
      const inMonth = (data.days || []).some((day) => day.business_date === data.today)
      selectedDate.value = inMonth && data.today ? data.today : (data.days[0] || {}).business_date || ''
      await loadDay(selectedDate.value)
    } catch (err) {
      if (seq !== calendarSeq) return
      errorText.value = err.message || '排班月历加载失败'
    } finally {
      if (seq === calendarSeq && !silent) loading.value = false
    }
  }

  async function loadDay(businessDate) {
    if (!businessDate) {
      dayDetail.value = null
      return
    }
    selectedDate.value = businessDate
    // 换一天先把上一天的人清掉（模板对 null 有「读取中…」兜底）：请求失败时表头已经是
    // 新那天、名单却还列着旧那天的人，店长会照着错的名单改班。
    dayDetail.value = null
    try {
      dayDetail.value = await api.get('/api/scheduling/day', { date: businessDate })
      // 成功就把上一次那句错误收掉：一条失败提示会一直挂着，看着像页面坏了。
      errorText.value = ''
    } catch (err) {
      errorText.value = err.message || '这一天读不出来'
    }
  }

  // ── 名单 + 待批 + 存假 ───────────────────────────────────────────────────
  async function loadRoster() {
    try {
      const data = await api.get('/api/scheduling/roster')
      roster.value = data.employees || []
      // 并集，不是替换：名单这份只有启用的班次，直接换上去会把「这个月还有行」的停用
      // 班次从月历的格子、图例和当天卡上挤掉（`mergeShiftList` 里写了为什么不能那么走）。
      if (data.shifts && data.shifts.length) shifts.value = mergeShiftList(shifts.value, data.shifts)
      // 工作区名单来自公共层（读的是卫生建的那份），卫生那边新建一个区，刷新就能选到。
      zones.value = data.zones || []
      if (data.max_cycle_days) maxCycleDays.value = data.max_cycle_days
    } catch (err) {
      errorText.value = err.message || '名单读不出来'
    }
  }

  /** 「还没配的人」= 在职（已批准、没停用）但一条规则都没有；坏规则也算。 */
  function needsRule(employee) {
    return (!employee.rule || employee.rule.invalid) && !employee.resigned && employee.approved
  }

  function countPending(employees) {
    return (employees || []).filter(needsRule).length
  }

  // 数人只碰计数，**不碰 `shifts`**：班次列归 `/calendar` 管（名单那份只有启用的）。
  // 读不出来时置 `null`（不是 0）：显示「全员都配好了」是假话。
  async function loadPendingCount() {
    try {
      const data = await api.get('/api/scheduling/roster')
      pendingCount.value = countPending(data.employees)
    } catch (err) {
      pendingCount.value = null
    }
  }

  // 月历上的待批角标：`/inbox` 每条申请带起止日期，按天摊开数一遍。
  // 读不出来就不标（空表）：宁可少一个角标，也不能在没读到的日子上标错数字。
  async function loadPendingMarks() {
    try {
      const data = await api.get('/api/scheduling/inbox')
      const marks = {}
      for (const request of data.requests || []) {
        // 换班只有一天（起止同值），请假是一段 —— 同一个展开函数覆盖两种。
        const days = eachDayInRange(
          request.start_date,
          request.end_date || request.start_date,
        )
        for (const day of days) marks[day] = (marks[day] || 0) + 1
      }
      pendingMarks.value = marks
      inboxRequests.value = data.requests || []
    } catch (err) {
      pendingMarks.value = {}
      inboxRequests.value = []
    }
  }

  /** 底下那根条与月历角标一起刷：批一条、撤回一条都会同时动到这两个。 */
  async function refreshNotes() {
    await Promise.all([loadPendingCount(), loadPendingMarks()])
  }

  // ── 周表 ─────────────────────────────────────────────────────────────────
  async function loadWeek(start) {
    weekError.value = ''
    try {
      // `start` 空着就交给服务端按「今天所在周」算：首屏还没读到 today 时也点得动。
      const data = await api.get('/api/scheduling/week', { start: start || '' })
      week.value = data
      weekStart.value = data.start
      // 并集，不是替换：周表这份带的是「这 7 天真有行的停用班次」，直接替换会把月历
      // 那边的班次列挤掉（`mergeShiftList` 里写了为什么不能那么走）。
      if (data.shifts && data.shifts.length) shifts.value = mergeShiftList(shifts.value, data.shifts)
    } catch (err) {
      weekError.value = err.message || '周表读不出来'
    }
  }

  async function loadLeaveBalances() {
    try {
      const data = await api.get('/api/attendance/sheet')
      const map = {}
      for (const person of data.employees || []) {
        const days = person.leave && person.leave.balance
        if (typeof days === 'number') map[person.id] = days
      }
      leaveBalance.value = map
      leaveMonth.value = data.month || ''
    } catch {
      // **静默失败**：存假是周表的附加信息，读不到就是不显示那一行小字，
      // 不该把整张周表拖成报错（周表本身才是这一页的主件）。
      leaveBalance.value = {}
      leaveMonth.value = ''
    }
  }

  // ── 刷新 helper ──────────────────────────────────────────────────────────
  /** 单日覆盖写完之后，月历那一份的人数跟着变（周表已经是最新的）。 */
  async function refreshMonthCounts() {
    if (!monthValue.value) return
    try {
      const data = await api.get('/api/scheduling/calendar', { month: monthValue.value })
      calendar.value = data
      if (data.shifts && data.shifts.length) shifts.value = mergeShiftList(shifts.value, data.shifts)
    } catch (err) {
      // 月历这一份下次进页面再补，不拿一句错盖住刚做成的改动。
    }
  }

  /** 写完重读：先把选中那天拨回「今天/1 号」，再把刚改的那天读回来。 */
  async function refreshAfterWrite(day) {
    await loadMonth(monthValue.value)
    if (day) await loadDay(day)
  }

  /** nudge 回调：按当前视角静默重读（月历 / 待批 / 周表）。 */
  async function refreshVisible() {
    await loadMonth(monthValue.value, true)
    await refreshNotes()
    // 周表开着的时候它也得跟着动：员工那边换了班，店长盯着的正是这张表。
    if (view.value === 'week') await loadWeek(weekStart.value)
  }

  // ── 写口（ADR 0142 第二步）───────────────────────────────────────────────
  // 落笔编排、单日覆盖与回执都收在这一层；页面只留笔 / 选区 / 编辑表单 / 弹窗 / 模板。
  // 这里仍不 import router：过去日确认框由页面模板渲染，board 只持 `pastConfirm`。
  const PICK_NOTICE_MS = 6000
  const notice = ref(null) // { text, details: [], warn } | null
  const noticeOpen = ref(false)
  let noticeTimer = null

  const pickBusy = ref(false)
  const pickUndo = ref(null) // { plan: [...] } | null
  const canUndo = computed(() => !!pickUndo.value && pickUndo.value.plan.length > 0)
  const PENDING_PAINT_LIMIT = 20
  const pendingPaints = ref([]) // [{ key, employee, day, action, options }]
  let drainingPaints = false
  const pastConfirm = ref(null) // { action, options, cells, dates, voiding, who } | null
  const editError = ref('')
  const overrideBusy = ref(false)

  // 页面为版式 / 图例也各留一份同名的三行小工具：这点重复换来两边各自可读，
  // 不值得为它再开一个模块（本文件仍是这一页唯一的编排层）。
  function employeeAt(employeeId) {
    return ((week.value && week.value.employees) || []).find((item) => item.id === employeeId) || null
  }

  function employeeName(employeeId) {
    const employee = employeeAt(employeeId)
    return employee ? employee.name : `#${employeeId}`
  }

  function shortDate(value) {
    const [, month, day] = String(value || '').split('-').map(Number)
    return month && day ? `${month}/${day}` : ''
  }

  function monthDayText(value) {
    const [, month, day] = String(value || '').split('-').map(Number)
    return month && day ? `${month}月${day}日` : ''
  }

  /** 周表那一格的当前值：撤销快照与跳过预判都要它（页面按员工对象取格，这里按 id 从 `week` 找）。 */
  function cellAt(employeeId, date) {
    const employee = employeeAt(employeeId)
    return employee && employee.cells ? employee.cells[date] || null : null
  }

  /** 这一格有没有班次（休 / 请假 / 空白都没有）：标「休半天」得有班次可挂。 */
  function cellHasShift(employee, day) {
    const cell = cellAt(employee && employee.id, day && day.business_date)
    return Boolean(cell && cell.scheduled && cell.shift_id !== null && cell.shift_id !== undefined)
  }

  /** 一行提示，6 秒后自己消失；新的那条替换旧的（逐格点名收进 `details`）。 */
  function sayNotice(text, details = [], warn = false) {
    if (noticeTimer) clearTimeout(noticeTimer)
    noticeTimer = null
    noticeOpen.value = false
    notice.value = text || details.length ? { text, details, warn } : null
    if (!notice.value) return
    noticeTimer = setTimeout(() => {
      notice.value = null
      noticeTimer = null
    }, PICK_NOTICE_MS)
  }

  /** 页面卸载时收掉提示的定时器（页面不再持 `noticeTimer`）。 */
  function dispose() {
    if (noticeTimer) clearTimeout(noticeTimer)
    noticeTimer = null
  }

  function skipText(items) {
    return items.map((item) => {
      const why = PICK_SKIP_TEXT[item.reason] || item.reason
      return `${employeeName(item.employeeId)} ${shortDate(item.date)} ${why}`
    }).join('、')
  }

  function skipContext(action) {
    return {
      action: action || PICK_SHIFT,
      today: week.value ? week.value.today : '',
      windowEnd: week.value ? week.value.window_end : '',
      cellOf: cellAt,
    }
  }

  // 待批提醒的数据来源：周表每格没有「待批」这个字段，只有既有的待办那一份（按天粒度）。
  const pendingCells = computed(() => {
    const map = {}
    for (const request of inboxRequests.value) {
      const days = eachDayInRange(request.start_date, request.end_date || request.start_date)
      for (const day of days) map[`${request.employee_id}|${day}`] = request.kind
    }
    return map
  })

  function reminderText(items) {
    const who = items.map((item) => `${employeeName(item.employeeId)} ${shortDate(item.date)}`).join('、')
    return `待批提醒：${who} 那天有申请等着批，批了会盖掉你刚刷的班。`
  }

  /** 一次落笔之后的回执（**一行**）：写了几格、跳过几格、能不能撤。
   *  逐格点名的那几行收进 `details`（「看全部」展开）—— 跳过与待批的信息不丢。 */
  function sayResult(result, cells, action, options, who = '') {
    const undoable = !!pickUndo.value && pickUndo.value.plan.length > 0
    const parts = []
    // 回执说清**这一笔是什么**：原来一律「已刷 N 格」，选错笔（清当成了休）也看不出来。
    // 「休半天」按这一笔实际写的值说话（单格就是那一格的新值，整片刷统一标上）。
    const verb = action === PICK_VOID
      ? '已清空'
      : action === PICK_RESTORE
        ? '已退回规则'
        : action === PICK_HALF_REST
          ? (options && options.halfRest ? '已标休半天' : '已取消休半天')
          : action === PICK_REST ? '已排休' : '已改班'
    if (result.applied) parts.push(`${verb} ${result.applied} 格${who ? ` · 写给 ${who}` : ''}`)
    if (result.skipped.length) parts.push(`跳过 ${result.skipped.length} 格`)
    if (!parts.length) parts.push('没有格子被改动')
    // 「撤销只留最近一批、只在这台手机上」这句说明跟着回执走（ADR 0107 决策 7）。
    const text = `${parts.join(' · ')}${undoable ? ' · 可撤销（只留最近一批、也只在这台手机上）' : ''}`
    const details = []
    if (result.skipped.length) details.push(`跳过：${skipText(result.skipped)}`)
    // 待批提醒：先信服务端回执里那一份（它知道谁那天真有申请），它没给就退回既有的待办那一份。
    let reminders = result.reminders
    if (!reminders.length) {
      const missed = new Set(result.skipped.map((item) => `${item.employeeId}|${item.date}`))
      reminders = pickPending(cells, pendingCells.value)
        .filter((item) => !missed.has(`${item.employeeId}|${item.date}`))
    }
    if (reminders.length) details.push(reminderText(reminders))
    sayNotice(text, details)
  }

  /** 连点不丢格：上一笔还在写时，点到的格子进队列，等这一笔落库后再按点击顺序写。
   *  同一格重复点只留最后一次（用户要的是最后那个班次）；上限 20，超了只提示、不排队。
   *  **不做乐观更新**：格子的样子仍以服务端回执 + 重读为准。 */
  function queuePaint(employee, day, action, options) {
    const key = `${employee.id}|${day.business_date}`
    const rest = pendingPaints.value.filter((item) => item.key !== key)
    if (rest.length >= PENDING_PAINT_LIMIT) {
      sayNotice('点太快了，等这一笔写完', [], true)
      return
    }
    rest.push({ key, employee, day, action, options })
    pendingPaints.value = rest
  }

  /** 按序把队列里的格子写完：每写完一格，`runPaint` 会再回来 drain 一次。 */
  async function drainPendingPaints() {
    if (drainingPaints || pickBusy.value) return
    drainingPaints = true
    try {
      while (pendingPaints.value.length && !pickBusy.value && !pastConfirm.value) {
        const next = pendingPaints.value[0]
        pendingPaints.value = pendingPaints.value.slice(1)
        await executePaint(next.employee, next.day, next.action, next.options)
        // 队列里的格子也可能是过去的日子：确认框先处理，余下的等下一笔完成再继续。
        if (pastConfirm.value) break
      }
    } finally {
      drainingPaints = false
    }
  }

  /** 真写一格（paintCell 与队列共用）：跳过预判 → requestPaint。 */
  async function executePaint(employee, day, action, options) {
    const cells = [{ employeeId: employee.id, date: day.business_date }]
    const [skip] = predictSkips(cells, skipContext(action))
    if (skip) {
      sayNotice(`跳过：${skipText([skip])}`)
      return
    }
    await requestPaint(action, options, cells)
  }

  /** 点一格 = 立刻用当前笔写这一格（票 14 修订口径 2）：不选区、不点「应用」，一步。
   *
   *  会被跳过的那种格子**不发请求**：超 30 天、已批的假（词表与回执同一套 `PICK_SKIP_TEXT`）。
   *  「休半天」还有一条本地闸：休 / 空白那格没有班次可挂，直接说一句、不发请求。
   *  整片刷的时候跳过由服务端回执点名（它才是权威），这里只是省掉一次注定被跳过的往返。
   *  **上一笔还在写时不再静默丢弃**：进队列，写完这一笔按顺序补（见上面 `queuePaint`）。 */
  async function paintCell(employee, day, action, options) {
    if (action === PICK_HALF_REST && !cellHasShift(employee, day)) {
      sayNotice(`跳过：${monthDayText(day.business_date)} 那天没有班次，标不上休半天`)
      return
    }
    if (pickBusy.value) {
      queuePaint(employee, day, action, options)
      return
    }
    await executePaint(employee, day, action, options)
  }

  /** 落笔前的三道闸（有没有格子 / 超没超 200 格 / 请求体全不全）；这一笔里有过去的日子
   *  再弹**一次**确认并点名是哪几天（ADR 0108 决策 3），确认之后才真写。 */
  async function requestPaint(action, options, cells, who = '') {
    if (!cells.length) return
    if (overLimit(cells)) {
      sayNotice(`一次最多 ${PICK_MAX_CELLS} 格，现在选了 ${cells.length} 格：缩小选区再刷。`, [], true)
      return
    }
    if (!batchPayload(action, options, cells)) {
      sayNotice('这一笔没有班次可写：先在笔栏上选一个班次。', [], true)
      return
    }
    const dates = pastDatesIn(cells, { today: week.value ? week.value.today : '' })
    if (dates.length) {
      pastConfirm.value = { action, options, cells, dates, voiding: action === PICK_VOID, who }
      return
    }
    await runPaint(action, options, cells, who)
  }

  /** 整片写（选区那一行的「刷」）：旧 `requestPaint` 的对外名字。 */
  async function paintRegion(action, options, cells, who = '') {
    await requestPaint(action, options, cells, who)
  }

  /** 选区那一行的「退回规则」：撤掉这一片的手动覆盖（action=restore）。 */
  async function restoreRegion(cells) {
    await requestPaint(PICK_RESTORE, {}, cells)
  }

  /** 过去日确认框的「照改 / 照样清空」：确认之后才真写。 */
  async function confirmPast() {
    const confirm = pastConfirm.value
    if (!confirm) return
    await runPaint(confirm.action, confirm.options, confirm.cells, confirm.who)
  }

  /** 过去日确认框的「再想想」。 */
  function cancelPast() {
    pastConfirm.value = null
  }

  /** 真写：一次请求写完（单格也只是 `items` 里的一条），写完只重拉一次周表。
   *  撤销要的是**应用前**的原值 —— 服务端不留快照（`staff_assignments` 是原地 upsert）。 */
  async function runPaint(action, options, cells, who = '') {
    pastConfirm.value = null
    pickBusy.value = true
    const snapshot = snapshotCells(cells, cellAt)
    try {
      const result = normalizeResult(
        await api.post('/api/scheduling/overrides/batch', batchPayload(action, options, cells)),
      )
      // 这一笔顶掉上一笔：撤销只留最近一批（ADR 0107 决策 7）。
      // 「休半天」那一笔改的不是班次，撤销按原值写回 half_rest（见 `undoPlan` 的第三个参数）。
      const plan = undoPlan(snapshot, result.skipped, action)
      pickUndo.value = plan.length ? { plan } : null
      sayResult(result, cells, action, options, who)
      await loadWeek(week.value ? week.value.start : weekStart.value)
      await refreshMonthCounts()
    } catch (err) {
      // 服务端把「为什么刷不上」说全了（超限、没这个班次…）：原话转过来，别拿一句「保存失败」盖掉。
      sayNotice(err.message || '这一笔没写上去', [], true)
    } finally {
      pickBusy.value = false
      void drainPendingPaints()
    }
  }

  /** 撤销刚才那一笔：逐格还原（原本没覆盖的走删除覆盖、原本有覆盖的写回原值）。 */
  async function undo() {
    const undoable = pickUndo.value
    if (!undoable || !undoable.plan.length) return
    pickBusy.value = true
    let undone = 0
    let kept = 0
    let processed = 0
    const failed = []
    const total = undoable.plan.length
    const UNDO_BATCH = 5
    sayNotice(`撤销中 0/${total}`)
    try {
      // 每批 5 格并发（原来逐格串行，19 格要等 19 个来回）；批与批之间仍按顺序。
      for (let start = 0; start < undoable.plan.length; start += UNDO_BATCH) {
        const batch = undoable.plan.slice(start, start + UNDO_BATCH)
        await Promise.all(batch.map(async (step) => {
          if (step.kind === 'keep') {
            // 那一格原本是**批出来的假**：覆盖行的 kind=leave 不是这两条接口写得回去的，
            // 如实说「撤不回来」，不拿一个「休」把它盖掉。
            kept += 1
            return
          }
          try {
            if (step.kind === 'clear') {
              await api.delete(`/api/scheduling/overrides/${step.employeeId}/${step.date}`)
            } else if (step.kind === 'half_rest') {
              // 那一笔只改了半天休：按原值发一条单格的批量（与落笔同一个端点，不多开一条路）。
              await api.post('/api/scheduling/overrides/batch', batchPayload(
                PICK_HALF_REST,
                { halfRest: step.payload.half_rest },
                [{ employeeId: step.employeeId, date: step.date }],
              ))
            } else {
              await api.put(`/api/scheduling/overrides/${step.employeeId}/${step.date}`, step.payload)
            }
            undone += 1
          } catch (err) {
            failed.push(step)
          }
        }))
        processed += batch.length
        if (processed < total) sayNotice(`撤销中 ${processed}/${total}`)
      }
      // 只留最近一批：撤过就没了（内存级，刷新或换设备也不在）。
      pickUndo.value = null
      sayNotice(`撤销中 ${total}/${total}`)
      await loadWeek(week.value ? week.value.start : weekStart.value)
      await refreshMonthCounts()
    } finally {
      pickBusy.value = false
      void drainPendingPaints()
    }
    const details = []
    // 失败逐格点名收进「看全部」（不是只报一个总数）。
    if (failed.length) {
      details.push(`没撤回来：${failed.map((step) => `${employeeName(step.employeeId)} ${shortDate(step.date)}`).join('、')}`)
    }
    if (kept) details.push(`有 ${kept} 格原本是批过的假，撤不回原样`)
    sayNotice(`已撤销 ${undone} 格`, details, failed.length > 0)
  }

  /** 单日覆盖（当天卡里的编辑器）：`shiftId === 'rest'` 走 `is_rest`，否则给班次 + 工作区。
   *  成功只返回 true，**重读交给页面**——旧页的顺序是先关对话框再重读，慢网时别让人干等；
   *  失败把服务端原话写进 `editError`。 */
  async function saveOverride({ employeeId, day, shiftId, zoneId } = {}) {
    overrideBusy.value = true
    editError.value = ''
    try {
      const payload = shiftId === 'rest'
        ? { is_rest: true }
        : { shift_id: Number(shiftId), zone_id: zoneId === '' ? null : Number(zoneId) }
      await api.put(`/api/scheduling/overrides/${employeeId}/${day}`, payload)
      return true
    } catch (err) {
      // 后端把「为什么改不了」说全了（过去的日子、还没铺到的天、休不能带班次…），
      // 原话转给超级管理员，别拿一句「保存失败」盖掉。
      editError.value = err.message || '这一天没改上'
      return false
    } finally {
      overrideBusy.value = false
    }
  }

  /** 撤销单日覆盖（回到规则）：同样只返回结果，重读交给页面。 */
  async function deleteOverride({ employeeId, day } = {}) {
    overrideBusy.value = true
    editError.value = ''
    try {
      await api.delete(`/api/scheduling/overrides/${employeeId}/${day}`)
      return true
    } catch (err) {
      editError.value = err.message || '撤销没成功'
      return false
    } finally {
      overrideBusy.value = false
    }
  }

  /** 换周时把上一批的撤销快照丢掉（它属于看不见的那一周）。 */
  function dropUndo() {
    pickUndo.value = null
  }

  return {
    // 状态
    loading,
    errorText,
    monthValue,
    calendar,
    selectedDate,
    dayDetail,
    shifts,
    week,
    weekStart,
    weekError,
    roster,
    zones,
    maxCycleDays,
    pendingCount,
    pendingMarks,
    inboxRequests,
    leaveBalance,
    leaveMonth,
    view,
    // 动作
    init,
    setView,
    loadMonth,
    loadDay,
    loadRoster,
    loadPendingCount,
    loadPendingMarks,
    loadWeek,
    loadLeaveBalances,
    refreshNotes,
    refreshMonthCounts,
    refreshAfterWrite,
    refreshVisible,
    // 写口状态（ADR 0142 第二步）
    notice,
    noticeOpen,
    pickBusy,
    canUndo,
    pendingPaints,
    pastConfirm,
    editError,
    overrideBusy,
    // 写口动作
    sayNotice,
    paintCell,
    paintRegion,
    restoreRegion,
    confirmPast,
    cancelPast,
    undo,
    saveOverride,
    deleteOverride,
    dropUndo,
    dispose,
  }
}
