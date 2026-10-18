/** 周表的**选区**与**批量落笔**（票 14 / ADR 0107「先选一片、再一次刷完」）。
 *
 *  要它解决什么：店里真实的批量动作只有两种 ——「给某个人刷一整周」与「把某一天全店刷成
 *  同一个班」，两个都是**行 / 列级**的事。票 12 那支常驻的笔要求「先配好笔、再逐格点」，
 *  一列 19 个人就是 19 次点击，于是这里换成两点式：点**日期列头**＝整列（全店那天）、
 *  点**人名**＝整行（这人这周）、点**一个格子**＝1 格；已经有选区时点另一个手柄就以锚点
 *  展开（列→列＝整列区间、格→格＝矩形、行→行＝整行区间、混合手柄按行 / 列各自取区间）。
 *  点回锚点本身、或点表格外的空白，都是取消。
 *
 *  这一层只放能脱离视图单测的东西：选区展开、影响格数、跳过预判、批量请求体、撤销计划。
 *  写盘那条路是后端新增的批量端点（`POST /api/scheduling/overrides/batch`）——
 *  一次校验、一次写锁、一次提交，「哪一格不能刷」的权威判据在服务端手里（周表每格只有
 *  `scheduled/shift_id/shift_name/zone_id/zone_name/overridden/leave` 七个键，连「今天
 *  是几号」都要靠外面喂）。所以这里的预判只是**给店长先看一眼**，回执一律以服务端为准。
 *
 *  两条日期口径跟着服务端走，前端只是镜像（镜错了会多说或少说一句，不会写坏数据）：
 *    · 过去只能补记 30 天（ADR 0108 决策 1）：`today - 30` 那天可改，再往前由服务端跳过；
 *    · 未来铺排窗口的末日由周表给（`week.window_end`），之后的日子还没铺。
 */

/** 一次最多 200 格（服务端上限，超了直接拒绝让你缩小选区）。 */
export const PICK_MAX_CELLS = 200

/** 过去可补记的天数（ADR 0108：30 天一处定义，这里只是同一口径的前端镜像）。 */
export const PICK_PAST_DAYS = 30

/** 三种动作，整批同一个。 */
export const PICK_SHIFT = 'shift'
export const PICK_REST = 'rest'
export const PICK_CLEAR = 'clear'

/** 服务端 `skipped[].reason` 的词表 → 回执里那句人话（词表定死，见 ADR 0107 决策 6）。 */
export const PICK_SKIP_TEXT = {
  past_day: '超出 30 天',
  // 2026-10-18 起不再产生（过去的日子也能清除）；词条留着兼容老服务端的错误码。
  past_clear: '过去的日子只能改成某个班（旧口径）',
  beyond_window: '还没铺到',
  leave_approved: '已批的假',
  out_of_roster: '不在册',
}

// ── 手柄与选区 ────────────────────────────────────────────────────────────
// 手柄是**身份**不是下标（日期 / 员工 id）：周表换一周、筛一次人之后下标会变，而手柄指的
// 还是同一列 / 同一个人。下标只在展开的那一刻按 `axes` 现算。

export function colHandle(date) {
  return { kind: 'col', date }
}

export function rowHandle(employeeId) {
  return { kind: 'row', employeeId }
}

export function cellHandle(employeeId, date) {
  return { kind: 'cell', employeeId, date }
}

export function sameHandle(a, b) {
  if (!a || !b) return false
  if (a.kind !== b.kind) return false
  if (a.kind === 'col') return a.date === b.date
  if (a.kind === 'row') return a.employeeId === b.employeeId
  return a.employeeId === b.employeeId && a.date === b.date
}

/** 点一个手柄之后的新选区；返回 `null` = 取消。
 *
 *  - 还没有选区 → 这个手柄**自己**的选区（整列 / 整行 / 1 格）；
 *  - 点回**锚点**本身 → 取消（ADR 0107 决策 3：找别的地方退出是多余的）；
 *  - 点别的手柄 → 锚点不动、目标换成它（于是「周三 → 周四 → 周五」能一直往右扩）。
 *
 *  `handle` 给空 = 点到表格外面的空白（视图把那一下翻成 `null`）。
 */
export function pickAt(selection, handle) {
  if (!handle) return null
  if (!selection) return { anchor: handle, target: handle }
  if (sameHandle(selection.anchor, handle)) return null
  return { anchor: selection.anchor, target: handle }
}

/** 两个手柄在**同一根轴**上各挑了哪一格：`null` = 这根轴上它不挑（整轴）。
 *
 *  选择器是「列头不挑行、行头不挑列、格子两样都挑」，于是两个手柄一合：
 *  列头 + 列头 = 整列区间（两端都不挑行）、格子 + 格子 = 矩形、列头 + 行头 = 一个格子。
 */
function rowSpec(handle) {
  return handle.kind === 'col' ? null : handle.employeeId
}

function colSpec(handle) {
  return handle.kind === 'row' ? null : handle.date
}

/** 这根轴上最终要哪几格：两端都不挑 = 整轴，只挑一端 = 那一格，两端都挑 = 两者之间。
 *
 *  找不到的 id（换了一周、或者被筛掉了）返回**空数组**而不是整轴：静默扩成整张表是最
 *  危险的兜底（手指点了一下，19 个人被刷掉）。
 */
function axisIndexes(specs, list) {
  const located = specs.map((id) => (id === null ? null : list.indexOf(id)))
  if (located.some((index, position) => specs[position] !== null && index < 0)) return []
  const [a, b] = located
  if (a === null && b === null) return list.map((_, index) => index)
  if (a === null) return [b]
  if (b === null) return [a]
  const out = []
  for (let index = Math.min(a, b); index <= Math.max(a, b); index += 1) out.push(index)
  return out
}

/** 选区 → 覆盖到的行 / 列 / 格子。
 *
 *  `axes` 是**渲染顺序**：`{ rows: [员工 id…], cols: [日期…] }`，行序就是周表给的那个
 *  （票 14 起由后端分片排序，前端一个字都不重排）。返回的 `cells` 也是同一顺序
 *  （先行后列），请求体直接照抄。
 */
export function pickRegion(selection, axes) {
  if (!selection || !selection.anchor || !selection.target) return null
  const rows = (axes && axes.rows) || []
  const cols = (axes && axes.cols) || []
  const anchor = selection.anchor
  const target = selection.target
  const rowIndexes = axisIndexes([rowSpec(anchor), rowSpec(target)], rows)
  const colIndexes = axisIndexes([colSpec(anchor), colSpec(target)], cols)
  const pickedRows = rowIndexes.map((index) => rows[index])
  const pickedCols = colIndexes.map((index) => cols[index])
  const cells = []
  for (const employeeId of pickedRows) {
    for (const date of pickedCols) cells.push({ employeeId, date })
  }
  return {
    anchor,
    target,
    // 手柄的档：两个列头 = 整列区间、两个人名 = 整行区间，其余（含混合）都按格子算。
    kind: anchor.kind === 'col' && target.kind === 'col'
      ? 'cols'
      : (anchor.kind === 'row' && target.kind === 'row' ? 'rows' : 'cells'),
    rows: pickedRows,
    cols: pickedCols,
    cells,
    count: cells.length,
    // 「整轴被**完整**覆盖」才算选到那一行 / 那一列：选一整列时只该有一个列头亮，而不是 19 个
    // 人名一起描框（那样比选区本身还抢眼，名字列看着像一排按钮）。矩形跨界覆盖满整轴时同样算。
    fullRows: pickedCols.length === cols.length ? pickedRows : [],
    fullCols: pickedRows.length === rows.length ? pickedCols : [],
  }
}

/** 一次刷的格数超过服务端上限：先让店长缩小选区，别发一个必然 400 的请求。 */
export function overLimit(cells) {
  return (cells || []).length > PICK_MAX_CELLS
}

// ── 日期 ──────────────────────────────────────────────────────────────────
// 只用 UTC 做加减（`toISOString` 取日期段）：营业日是没有时区的字符串，
// 用本地 `Date` 加减会在夏令时地区漂一天。

export function shiftDate(value, days) {
  const [year, month, day] = String(value || '').split('-').map(Number)
  if (!year || !month || !day) return ''
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10)
}

/** 可补记窗口的最早一天（含）：比它更早的日子服务端按 `past_day` 跳过。 */
export function pastFloor(today, pastDays = PICK_PAST_DAYS) {
  return today ? shiftDate(today, -Math.abs(pastDays)) : ''
}

// ── 跳过预判（服务端仍是权威）────────────────────────────────────────────
// 词表见 PICK_SKIP_TEXT。「人不在册」预判不了：周表只给在册的人（被过滤掉的人只有一个
// 计数），所以那一类交给服务端说。

/** 这一格会被跳过的原因；不会跳过就返回空串。
 *
 *  `ctx = { action, today, windowEnd, pastDays, cellOf(employeeId, date) }`——
 *  `cellOf` 取周表那一格（要它的 `leave`）。
 */
export function skipReason(cell, ctx) {
  const { action, today, windowEnd } = ctx || {}
  const pastDays = (ctx && ctx.pastDays) || PICK_PAST_DAYS
  if (!cell) return ''
  if (windowEnd && cell.date > windowEnd) return 'beyond_window'
  const data = ctx && ctx.cellOf ? ctx.cellOf(cell.employeeId, cell.date) : null
  if (data && data.leave) return 'leave_approved'
  const floor = pastFloor(today, pastDays)
  if (floor && cell.date < floor) return 'past_day'
  return ''
}

/** 选区里**会被跳过**的格子（照 `cells` 的顺序逐格点名）。 */
export function predictSkips(cells, ctx) {
  const out = []
  for (const cell of cells || []) {
    const reason = skipReason(cell, ctx)
    if (reason) out.push({ employeeId: cell.employeeId, date: cell.date, reason })
  }
  return out
}

/** 选区里**已经过去、但还在可补记窗口内**的日子（升序去重）。
 *
 *  非空 = 点应用要先弹**一次**确认并点名是哪几天（ADR 0108 决策 3）——不是每格弹一次。
 *  超出窗口的日子不算在里面：它们根本不写，问也白问。
 */
export function pastDatesIn(cells, ctx) {
  const { today } = ctx || {}
  if (!today) return []
  const pastDays = (ctx && ctx.pastDays) || PICK_PAST_DAYS
  const floor = pastFloor(today, pastDays)
  const days = new Set()
  for (const cell of cells || []) {
    if (cell.date < today && cell.date >= floor) days.add(cell.date)
  }
  return [...days].sort()
}

/** 选区里**那天有申请等着批**的格子（点名提醒，但照刷）。
 *
 *  数据来自既有的 `GET /api/scheduling/inbox`：周表每格没有「待批」这个字段，只有那份
 *  待办里有（按天粒度，取选区覆盖到的日期）。`pending` 是
 *  `{ '<员工 id>|<日期>': kind }`——kind 是 `leave` / `swap`。
 */
export function pickPending(cells, pending) {
  const out = []
  for (const cell of cells || []) {
    const kind = (pending || {})[`${cell.employeeId}|${cell.date}`]
    if (kind) out.push({ employeeId: cell.employeeId, date: cell.date, kind })
  }
  return out
}

// ── 请求体 ────────────────────────────────────────────────────────────────

/** 一批一次写完的请求体（`POST /api/scheduling/overrides/batch`）。
 *
 *  `action='shift'` 没给班次时返回 `null`（调用方据此提示，不发请求）：写一个「没有班次的
 *  覆盖」既不是休也不是某个班，服务端只能拒。`zoneId` 给 `''`（跟固定区）时翻成 `null`，
 *  后端会把那个班次的固定区回填进来 —— 批量时这正是默认：19 个人各有各的固定区，硬指
 *  一个区通常没意义。
 */
export function batchPayload(action, options, cells) {
  const items = (cells || []).map((cell) => ({
    employee_id: Number(cell.employeeId),
    business_date: cell.date,
  }))
  if (!items.length) return null
  if (action === PICK_SHIFT) {
    const shiftId = options && options.shiftId
    if (shiftId === null || shiftId === undefined || shiftId === '') return null
    const zoneId = options ? options.zoneId : null
    return {
      action: PICK_SHIFT,
      shift_id: Number(shiftId),
      zone_id: zoneId === '' || zoneId === null || zoneId === undefined ? null : Number(zoneId),
      items,
    }
  }
  if (action === PICK_REST || action === PICK_CLEAR) return { action, items }
  return null
}

/** 服务端回执 → 界面用的形状（API 是 snake_case，这里只翻一次，别让每个读取点各翻一遍）。 */
export function normalizeResult(result) {
  return {
    applied: Number((result && result.applied) || 0),
    skipped: ((result && result.skipped) || []).map((item) => ({
      employeeId: Number(item.employee_id),
      date: item.business_date,
      reason: item.reason,
    })),
    reminders: ((result && result.reminders) || []).map((item) => ({
      employeeId: Number(item.employee_id),
      date: item.business_date,
      kind: item.kind,
    })),
  }
}

// ── 撤销（只留最近一批、内存级）────────────────────────────────────────────
// ADR 0107 决策 7 / ADR 0108 决策 5：应用前把选区内每格的**原值**拿在手上，点 ↺ 逐格还原；
// 刷新页面或换一台手机就没了 —— 服务端不留快照（`staff_assignments` 是原地 upsert）。

/** 应用**之前**记下选区内每一格的当前值（调用方只保留最近一批）。 */
export function snapshotCells(cells, cellOf) {
  return (cells || []).map((cell) => {
    const data = (cellOf ? cellOf(cell.employeeId, cell.date) : null) || {}
    return {
      employeeId: cell.employeeId,
      date: cell.date,
      scheduled: !!data.scheduled,
      overridden: !!data.overridden,
      leave: !!data.leave,
      shift_id: data.shift_id === undefined ? null : data.shift_id,
      zone_id: data.zone_id === undefined ? null : data.zone_id,
    }
  })
}

/** 撤销计划：原本**没有**覆盖的走「删掉覆盖」（那天回到规则铺出来的样子），原本**有**覆盖的
 *  把原值写回去。两条都是既有接口（`PUT` / `DELETE /api/scheduling/overrides/{人}/{天}`）。
 *
 *  `skipped` 是服务端回执里那一份：**被跳过的格子没被写过，还原它们反而会改坏**（比如把
 *  一个「还没铺到」的格子写成休），所以整个不进计划。
 *  返回项的 `kind`：`clear`（删覆盖）/ `restore`（写回原值）/ `keep`（撤不回来：那一格原本是
 *  **批出来的假**，覆盖行的 kind=leave 不是这两条接口写得回去的），如实说，不假装撤掉了。
 */
export function undoPlan(snapshot, skipped) {
  const missed = new Set((skipped || []).map((item) => `${item.employeeId}|${item.date}`))
  const plan = []
  for (const item of snapshot || []) {
    if (missed.has(`${item.employeeId}|${item.date}`)) continue
    if (!item.overridden) {
      plan.push({ employeeId: item.employeeId, date: item.date, kind: 'clear' })
      continue
    }
    if (item.leave) {
      plan.push({ employeeId: item.employeeId, date: item.date, kind: 'keep' })
      continue
    }
    plan.push({
      employeeId: item.employeeId,
      date: item.date,
      kind: 'restore',
      payload: item.shift_id === null || item.shift_id === undefined
        ? { is_rest: true }
        : {
            shift_id: Number(item.shift_id),
            zone_id: item.zone_id === null || item.zone_id === undefined ? null : Number(item.zone_id),
          },
    })
  }
  return plan
}
