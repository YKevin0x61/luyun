/** 周表的**选区**与**批量落笔**（票 14 / ADR 0107「先选一片、再一次刷完」）。
 *
 *  要它解决什么：店里真实的批量动作只有两种 ——「给某个人刷一整周」与「把某一天全店刷成
 *  同一个班」，两个都是**行 / 列级**的事。票 12 那支常驻的笔要求「先配好笔、再逐格点」，
 *  一列 19 个人就是 19 次点击，于是这里换成两点式：点**日期列头**＝整列（全店那天）、
 *  点**人名**＝整行（这人这周）、点**一个格子**＝1 格；已经有选区时点另一个手柄就以锚点
 *  展开（列→列＝整列区间、格→格＝矩形、行→行＝整行区间、混合手柄按行 / 列各自取区间）。
 *  点回锚点本身、或点表格外的空白，都是取消。
 *
 *  这一层只放能脱离视图单测的东西：选区展开、影响格数、批量请求体、撤销计划、过去日
 *  确认。写盘那条路是后端新增的批量端点（`POST /api/scheduling/overrides/batch`）——
 *  一次校验、一次写锁、一次提交。
 *
 *  **「哪一格不能刷」的判据只有服务端一份**（`services/scheduling/paint_gate.py`：在册 /
 *  离职 / 可补记窗口 / 铺排窗口 / 已批的假 / 那天有没有班次），前端不再复刻一遍：会跳过
 *  哪几格、为什么，看回执的 `skipped[]`。前端只留两件**数得清**的事 ——
 *    · 一次最多几格、能倒着改几天：由 `/week` 下发的 `limits`（`max_cells` / `past_days`）
 *      给出，那两个数只定义在落笔闸里，前端不写死；
 *    · 这一笔里有过去的日子就弹**一次**确认并点名（ADR 0108 决策 3）—— 是「你确定吗」，
 *      不是「能不能写」。
 */

/** 五种动作，整批同一个。
 *
 *  · `shift` / `rest`：改成某个班 / 改成休（原有）；
 *  · `void`：把这一天**清空**（空白 = 还没排到，规则展开也不会铺回来，2026-10-29 用户裁定）；
 *  · `restore`：退回轮转规则（原来那颗「清」的语义；服务端仍收 `clear` 作别名）；
 *  · `half_rest`：设 / 清这一天的「休半天」（ADR 0114；顶层带 `half_rest: bool`）。
 */
export const PICK_SHIFT = 'shift'
export const PICK_REST = 'rest'
export const PICK_VOID = 'void'
export const PICK_RESTORE = 'restore'
export const PICK_HALF_REST = 'half_rest'

/** 服务端 `skipped[].reason` 的词表 → 回执里那句人话。
 *
 *  码本身定义在 `services/scheduling/paint_gate.py`（判据与词表同一处）；这里只是**怎么
 *  说给店长听**，多一个码不认识就原样显示，不至于显示空白。
 */
export const PICK_SKIP_TEXT = {
  past_day: '超出可补记窗口',
  // 2026-10-18 起不再产生（过去的日子也能清除）；词条留着兼容老服务端的错误码。
  past_clear: '过去的日子只能改成某个班（旧口径）',
  beyond_window: '还没铺到',
  leave_approved: '已批的假',
  out_of_roster: '不在册',
  // 离职（批量口）：离职日之后的日子不再排班，当天及以前仍可补记。
  resigned_employee: '已离职',
  // 休 / 空白那格没有班次可标半天休（周表点「休半天」笔时本地就先拦下来，不会发请求）。
  half_rest_needs_shift: '那天没有班次，标不上休半天',
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

/** 一次刷的格数超过服务端上限：先让店长缩小选区，别发一个必然 400 的请求。
 *
 *  `maxCells` 由 `/week` 的 `limits` 给（服务端那一份常量）。拿不到（还没拉到周表）就
 *  不挡 —— 宁可由服务端按自己的口径拒，也不在前端猜一个数。 */
export function overLimit(cells, maxCells) {
  const limit = Number(maxCells)
  if (!(limit > 0)) return false
  return (cells || []).length > limit
}

// ── 日期 ──────────────────────────────────────────────────────────────────
// 只用 UTC 做加减（`toISOString` 取日期段）：营业日是没有时区的字符串，
// 用本地 `Date` 加减会在夏令时地区漂一天。

export function shiftDate(value, days) {
  const [year, month, day] = String(value || '').split('-').map(Number)
  if (!year || !month || !day) return ''
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10)
}

/** 可补记窗口的最早一天（含）：比它更早的日子服务端按 `past_day` 跳过。
 *
 *  `pastDays` 由 `/week` 的 `limits.past_days` 给；没给就返回空串（= 不下界）。
 */
export function pastFloor(today, pastDays) {
  const days = Number(pastDays)
  if (!today || !(days > 0)) return ''
  return shiftDate(today, -days)
}

// ── 过去日确认（是「你确定吗」，不是「能不能写」）──────────────────────────

/** 选区里**已经过去、但还在可补记窗口内**的日子（升序去重）。
 *
 *  非空 = 点应用要先弹**一次**确认并点名是哪几天（ADR 0108 决策 3）——不是每格弹一次。
 *  拿不到 `pastDays` 时下界为空：过去的日子**全部**要确认（宁可多问一句，也不静默写）。
 */
export function pastDatesIn(cells, ctx) {
  const { today } = ctx || {}
  if (!today) return []
  const floor = pastFloor(today, ctx && ctx.pastDays)
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
 *
 *  `action='half_rest'` 必须显式给布尔 `options.halfRest`（设 = true、取消 = false）：
 *  整批同一个值，单格时调用方用那一格的现值取反。
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
  if (action === PICK_HALF_REST) {
    const halfRest = options ? options.halfRest : undefined
    if (typeof halfRest !== 'boolean') return null
    return { action: PICK_HALF_REST, half_rest: halfRest, items }
  }
  if (action === PICK_REST || action === PICK_VOID || action === PICK_RESTORE) {
    return { action, items }
  }
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

/** 应用**之前**记下选区内每一格的当前值（调用方只保留最近一批）。
 *
 *  `half_rest` 是「休半天」的原值（撤销 `half_rest` 那一笔要按它写回）。周表每格只有
 *  `scheduled/shift_id/shift_name/zone_id/zone_name/overridden/leave`（契约里的七个键），
 *  没有 `voided` —— 撤销「清空」靠 `overridden` 分两条现有路径还原，不需要它。
 */
export function snapshotCells(cells, cellOf) {
  return (cells || []).map((cell) => {
    const data = (cellOf ? cellOf(cell.employeeId, cell.date) : null) || {}
    return {
      employeeId: cell.employeeId,
      date: cell.date,
      scheduled: !!data.scheduled,
      overridden: !!data.overridden,
      leave: !!data.leave,
      half_rest: !!data.half_rest,
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
 *
 *  `action` 是刚写下去的那一笔的动作：只有 `half_rest` 需要它 —— 那一笔改的是半天休标志，
 *  班次 / 工作区原样没动，所以按格子原值写回 `half_rest`（新增 `half_rest` 这一种计划项），
 *  不能拿「写回原班次」那条路盖上去（那一笔写班次时不会顺带把半天休改回来）。
 */
export function undoPlan(snapshot, skipped, action) {
  const missed = new Set((skipped || []).map((item) => `${item.employeeId}|${item.date}`))
  const plan = []
  for (const item of snapshot || []) {
    if (missed.has(`${item.employeeId}|${item.date}`)) continue
    if (action === PICK_HALF_REST) {
      plan.push({
        employeeId: item.employeeId,
        date: item.date,
        kind: 'half_rest',
        payload: { half_rest: !!item.half_rest },
      })
      continue
    }
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
