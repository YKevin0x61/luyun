// @vitest-environment node
import { describe, expect, it } from 'vitest'

import {
  PICK_HALF_REST,
  PICK_MAX_CELLS,
  PICK_REST,
  PICK_RESTORE,
  PICK_SHIFT,
  PICK_SKIP_TEXT,
  PICK_VOID,
  batchPayload,
  cellHandle,
  colHandle,
  normalizeResult,
  overLimit,
  pastDatesIn,
  pastFloor,
  pickAt,
  pickPending,
  pickRegion,
  predictSkips,
  rowHandle,
  sameHandle,
  shiftDate,
  snapshotCells,
  undoPlan,
} from '../schedulingWeekPick'

// 一张 3 人 × 3 天的小表：行序 / 列序**由外面给**（票 14 起后端分片排序，前端不重排），
// 所以这里的顺序就是断言的一部分。
const AXES = {
  rows: [7, 9, 11],
  cols: ['2026-10-12', '2026-10-13', '2026-10-14'],
}

/** 两点式：先点 anchor，再点 target（走的是视图同一条 `pickAt` 路径）。 */
function twoPoints(anchor, target) {
  return pickAt(pickAt(null, anchor), target)
}

describe('周表选区：点一个手柄选什么', () => {
  it('还没有选区时：点列头 = 整列（全店那天）', () => {
    const region = pickRegion(pickAt(null, colHandle('2026-10-13')), AXES)
    expect(region.kind).toBe('cols')
    expect(region.rows).toEqual([7, 9, 11])
    expect(region.cols).toEqual(['2026-10-13'])
    expect(region.count).toBe(3)
    expect(region.cells).toEqual([
      { employeeId: 7, date: '2026-10-13' },
      { employeeId: 9, date: '2026-10-13' },
      { employeeId: 11, date: '2026-10-13' },
    ])
  })

  it('还没有选区时：点人名 = 这人整周；点格子 = 1 格', () => {
    const row = pickRegion(pickAt(null, rowHandle(9)), AXES)
    expect(row.kind).toBe('rows')
    expect(row.rows).toEqual([9])
    expect(row.cols).toEqual(AXES.cols)
    expect(row.count).toBe(3)

    const cell = pickRegion(pickAt(null, cellHandle(9, '2026-10-14')), AXES)
    expect(cell.kind).toBe('cells')
    expect(cell.cells).toEqual([{ employeeId: 9, date: '2026-10-14' }])
    expect(cell.count).toBe(1)
  })

  it('格子顺序 = 行序 × 列序（请求体直接照抄，前端不重排）', () => {
    const region = pickRegion(twoPoints(cellHandle(9, '2026-10-12'), cellHandle(11, '2026-10-13')), AXES)
    expect(region.cells).toEqual([
      { employeeId: 9, date: '2026-10-12' },
      { employeeId: 9, date: '2026-10-13' },
      { employeeId: 11, date: '2026-10-12' },
      { employeeId: 11, date: '2026-10-13' },
    ])
  })
})

describe('周表选区：两点式扩范围（ADR 0107 决策 1）', () => {
  it('列头 → 列头 = 两整列（锚点不动，可以继续往右扩）', () => {
    let selection = pickAt(null, colHandle('2026-10-12'))
    selection = pickAt(selection, colHandle('2026-10-13'))
    expect(pickRegion(selection, AXES).cols).toEqual(['2026-10-12', '2026-10-13'])
    expect(pickRegion(selection, AXES).count).toBe(6)
    // 第三次点周五：还是从**锚点**（周一）展开，不是从周二
    selection = pickAt(selection, colHandle('2026-10-14'))
    expect(pickRegion(selection, AXES).cols).toEqual(AXES.cols)
    expect(pickRegion(selection, AXES).count).toBe(9)
    // 反着点也成立（先点周五再点周一，区间是同一片）
    const back = twoPoints(colHandle('2026-10-14'), colHandle('2026-10-12'))
    expect(pickRegion(back, AXES).cols).toEqual(AXES.cols)
  })

  it('行头 → 行头 = 整行区间', () => {
    const selection = twoPoints(rowHandle(7), rowHandle(11))
    const region = pickRegion(selection, AXES)
    expect(region.kind).toBe('rows')
    expect(region.rows).toEqual([7, 9, 11])
    expect(region.count).toBe(9)
  })

  it('格子 → 格子 = 以两格为对角的矩形', () => {
    const selection = twoPoints(cellHandle(7, '2026-10-13'), cellHandle(11, '2026-10-12'))
    const region = pickRegion(selection, AXES)
    expect(region.rows).toEqual([7, 9, 11])
    expect(region.cols).toEqual(['2026-10-12', '2026-10-13'])
    expect(region.count).toBe(6)
    expect(region.kind).toBe('cells')
  })

  it('混合手柄各取各的区间：列头 + 格子 = 那一行上的一段', () => {
    // 先点周三的列头（全店），再点张三周四那一格 → 只留下张三，列是周三…周四
    const selection = twoPoints(colHandle('2026-10-13'), cellHandle(7, '2026-10-14'))
    const region = pickRegion(selection, AXES)
    expect(region.rows).toEqual([7])
    expect(region.cols).toEqual(['2026-10-13', '2026-10-14'])
    expect(region.count).toBe(2)
  })

  it('混合手柄：列头 + 行头 = 交叉的那一格', () => {
    const region = pickRegion(twoPoints(colHandle('2026-10-14'), rowHandle(9)), AXES)
    expect(region.cells).toEqual([{ employeeId: 9, date: '2026-10-14' }])
  })

  it('混合手柄：格子 + 行头 = 那一列上的一段（从这一格往下扩）', () => {
    const region = pickRegion(twoPoints(cellHandle(7, '2026-10-14'), rowHandle(11)), AXES)
    expect(region.rows).toEqual([7, 9, 11])
    expect(region.cols).toEqual(['2026-10-14'])
    expect(region.count).toBe(3)
  })

  it('点回锚点本身 = 取消；点目标那一头不取消（锚点还在）', () => {
    const selection = twoPoints(colHandle('2026-10-12'), colHandle('2026-10-13'))
    expect(pickAt(selection, colHandle('2026-10-12'))).toBeNull()
    const again = pickAt(selection, colHandle('2026-10-13'))
    expect(again).not.toBeNull()
    expect(pickRegion(again, AXES).cols).toEqual(['2026-10-12', '2026-10-13'])
  })

  it('点表格外 = 取消（视图把那一下翻成空手柄）', () => {
    const selection = twoPoints(colHandle('2026-10-12'), colHandle('2026-10-13'))
    expect(pickAt(selection, null)).toBeNull()
    expect(pickRegion(null, AXES)).toBeNull()
  })

  it('手柄认的是身份（日期 / 员工 id），下标交给这张表现算', () => {
    expect(sameHandle(colHandle('2026-10-12'), colHandle('2026-10-12'))).toBe(true)
    expect(sameHandle(cellHandle(7, '2026-10-12'), rowHandle(7))).toBe(false)
    expect(sameHandle(null, rowHandle(7))).toBe(false)
    // 换一周：同一个列头的日期不在表里了 → 这一片选不到东西（不静默扩成整张表）
    const stale = pickAt(null, colHandle('2026-09-28'))
    const region = pickRegion(stale, AXES)
    expect(region.cols).toEqual([])
    expect(region.count).toBe(0)
  })

  it('筛人之后那几行不在表里：选区跟着缩，不偷偷刷看不见的人', () => {
    const filtered = { rows: [9], cols: AXES.cols }
    const region = pickRegion(twoPoints(rowHandle(7), rowHandle(11)), filtered)
    expect(region.rows).toEqual([])
    expect(region.count).toBe(0)
  })
})

describe('周表选区：影响格数与上限', () => {
  it('格数就是 cells 的长度；超过 200 格先拦下来', () => {
    const big = { rows: Array.from({ length: 30 }, (_, i) => i + 1), cols: AXES.cols }
    const region = pickRegion(pickAt(null, colHandle('2026-10-14')), big)
    expect(region.count).toBe(30)
    expect(overLimit(region.cells)).toBe(false)
    const rows = Array.from({ length: PICK_MAX_CELLS + 1 }, (_, i) => i + 1)
    expect(overLimit(rows.map((employeeId) => ({ employeeId, date: '2026-10-14' })))).toBe(true)
    expect(overLimit(region.cells)).toBe(false)
  })
})

describe('周表选区：跳过预判（服务端仍是权威）', () => {
  const ctx = {
    action: PICK_SHIFT,
    today: '2026-10-14',
    windowEnd: '2026-12-31',
    pastDays: 30,
    cellOf: (employeeId, date) => (employeeId === 9 && date === '2026-10-13' ? { leave: true } : null),
  }

  it('超出 30 天 = past_day；窗口内（含第 30 天）不算', () => {
    expect(pastFloor('2026-10-14')).toBe('2026-09-14')
    expect(shiftDate('2026-10-14', -30)).toBe('2026-09-14')
    expect(predictSkips([{ employeeId: 7, date: '2026-09-14' }], ctx)).toEqual([])
    expect(predictSkips([{ employeeId: 7, date: '2026-09-13' }], ctx))
      .toEqual([{ employeeId: 7, date: '2026-09-13', reason: 'past_day' }])
  })

  it('超出未来铺排窗口 = beyond_window', () => {
    expect(predictSkips([{ employeeId: 7, date: '2027-01-01' }], ctx))
      .toEqual([{ employeeId: 7, date: '2027-01-01', reason: 'beyond_window' }])
  })

  it('那天有已批的假 = leave_approved（照 `cells` 顺序逐格点名）', () => {
    const items = predictSkips([
      { employeeId: 7, date: '2026-10-13' },
      { employeeId: 9, date: '2026-10-13' },
    ], ctx)
    expect(items).toEqual([{ employeeId: 9, date: '2026-10-13', reason: 'leave_approved' }])
  })

  it('过去的日子上执行清除**不再跳过**（2026-10-18 用户裁定：清也能清过去的）', () => {
    // 放开 ADR 0108 决策 6 的「过去只能改成某个班」：清空与改班次同一条下界
    // （可补记窗口），窗口内过去的日子照常写，超出窗口才是 past_day。
    const clear = { ...ctx, action: PICK_VOID }
    expect(predictSkips([{ employeeId: 7, date: '2026-10-13' }], clear)).toEqual([])
    expect(predictSkips([{ employeeId: 7, date: '2026-10-13' }], ctx)).toEqual([])
    // 今天清除照旧
    expect(predictSkips([{ employeeId: 7, date: '2026-10-14' }], clear)).toEqual([])
    // 超窗口仍然是 past_day（窗口是数据保留口径，不随这次放开）
    expect(predictSkips([{ employeeId: 7, date: '2026-08-01' }], clear))
      .toEqual([{ employeeId: 7, date: '2026-08-01', reason: 'past_day' }])
  })

  it('过去但还在窗口内的日子点出来（点应用要弹一次确认）', () => {
    const cells = [
      { employeeId: 7, date: '2026-10-12' },
      { employeeId: 9, date: '2026-10-12' },
      { employeeId: 7, date: '2026-10-14' },
      { employeeId: 7, date: '2026-09-01' },
    ]
    expect(pastDatesIn(cells, { today: '2026-10-14' })).toEqual(['2026-10-12'])
    expect(pastDatesIn(cells, { today: '' })).toEqual([])
  })

  it('待批提醒从既有的待办接口取（按天粒度），照样刷', () => {
    const cells = [
      { employeeId: 7, date: '2026-10-14' },
      { employeeId: 9, date: '2026-10-15' },
    ]
    const pending = { '9|2026-10-15': 'leave', '7|2026-10-15': 'swap' }
    expect(pickPending(cells, pending)).toEqual([{ employeeId: 9, date: '2026-10-15', kind: 'leave' }])
    expect(pickPending(cells, {})).toEqual([])
  })
})

describe('周表选区：批量请求体（POST /api/scheduling/overrides/batch）', () => {
  const cells = [
    { employeeId: 7, date: '2026-10-12' },
    { employeeId: 9, date: '2026-10-12' },
  ]

  it('五种动作各自的形状：班次要班次，其余各带自己的顶层参数', () => {
    expect(batchPayload(PICK_SHIFT, { shiftId: '3', zoneId: '5' }, cells)).toEqual({
      action: 'shift',
      shift_id: 3,
      zone_id: 5,
      items: [
        { employee_id: 7, business_date: '2026-10-12' },
        { employee_id: 9, business_date: '2026-10-12' },
      ],
    })
    expect(batchPayload(PICK_SHIFT, { shiftId: 3, zoneId: '' }, cells).zone_id).toBeNull()
    expect(batchPayload(PICK_SHIFT, { shiftId: 3, zoneId: null }, cells).zone_id).toBeNull()
    expect(batchPayload(PICK_REST, {}, cells)).toEqual({
      action: 'rest',
      items: [
        { employee_id: 7, business_date: '2026-10-12' },
        { employee_id: 9, business_date: '2026-10-12' },
      ],
    })
    // 「清」笔清空（空白），「退回规则」撤销手改 —— 两颗棋子两个动作，不能再混成一个 clear。
    expect(batchPayload(PICK_VOID, {}, cells)).toEqual({
      action: 'void',
      items: [
        { employee_id: 7, business_date: '2026-10-12' },
        { employee_id: 9, business_date: '2026-10-12' },
      ],
    })
    expect(batchPayload(PICK_RESTORE, {}, cells).action).toBe('restore')
  })

  it('「休半天」带布尔的顶层 half_rest；不给 / 给错类型就别发请求', () => {
    expect(batchPayload(PICK_HALF_REST, { halfRest: true }, cells)).toEqual({
      action: 'half_rest',
      half_rest: true,
      items: [
        { employee_id: 7, business_date: '2026-10-12' },
        { employee_id: 9, business_date: '2026-10-12' },
      ],
    })
    expect(batchPayload(PICK_HALF_REST, { halfRest: false }, cells).half_rest).toBe(false)
    expect(batchPayload(PICK_HALF_REST, {}, cells)).toBeNull()
    expect(batchPayload(PICK_HALF_REST, { halfRest: null }, cells)).toBeNull()
    expect(batchPayload(PICK_HALF_REST, { halfRest: 'true' }, cells)).toBeNull()
  })

  it('没选班次 / 没选格子就别发请求（返回 null，由调用方说一句）', () => {
    expect(batchPayload(PICK_SHIFT, { shiftId: null }, cells)).toBeNull()
    expect(batchPayload(PICK_SHIFT, { shiftId: '' }, cells)).toBeNull()
    expect(batchPayload(PICK_SHIFT, { shiftId: 3 }, [])).toBeNull()
    expect(batchPayload('paint', {}, cells)).toBeNull()
  })

  it('跳过词表补了「休半天要有班次」这句人话', () => {
    expect(PICK_SKIP_TEXT.half_rest_needs_shift).toBe('那天没有班次，标不上休半天')
  })

  it('回执翻成界面形状：snake_case 只在这里翻一次', () => {
    expect(normalizeResult({
      applied: 17,
      skipped: [{ employee_id: 5, business_date: '2026-10-13', reason: 'leave_approved' }],
      reminders: [{ employee_id: 9, business_date: '2026-10-15', kind: 'pending_request' }],
    })).toEqual({
      applied: 17,
      skipped: [{ employeeId: 5, date: '2026-10-13', reason: 'leave_approved' }],
      reminders: [{ employeeId: 9, date: '2026-10-15', kind: 'pending_request' }],
    })
    expect(normalizeResult(null)).toEqual({ applied: 0, skipped: [], reminders: [] })
  })
})

describe('周表选区：撤销只留最近一批（ADR 0107 决策 7）', () => {
  const cells = [
    { employeeId: 7, date: '2026-10-12' },
    { employeeId: 9, date: '2026-10-12' },
    { employeeId: 11, date: '2026-10-13' },
  ]
  const weekCells = {
    '7|2026-10-12': { scheduled: true, overridden: false, shift_id: 3, zone_id: 5 },
    // 这一格已经是半天休：撤销「休半天」那一笔要按原值写回 true。
    '9|2026-10-12': { scheduled: true, overridden: true, half_rest: true, shift_id: 4, zone_id: 6 },
    '11|2026-10-13': { scheduled: true, overridden: true, leave: true, shift_id: null, zone_id: null },
  }
  const cellOf = (employeeId, date) => weekCells[`${employeeId}|${date}`]

  it('应用前记下每格的原值（含 overridden / 班次 / 区 / 休 / 假 / 休半天）', () => {
    expect(snapshotCells(cells, cellOf)).toEqual([
      { employeeId: 7, date: '2026-10-12', scheduled: true, overridden: false, leave: false, half_rest: false, shift_id: 3, zone_id: 5 },
      { employeeId: 9, date: '2026-10-12', scheduled: true, overridden: true, leave: false, half_rest: true, shift_id: 4, zone_id: 6 },
      { employeeId: 11, date: '2026-10-13', scheduled: true, overridden: true, leave: true, half_rest: false, shift_id: null, zone_id: null },
    ])
    // 那一格读不出来（不在当周）时给空值，不抛
    expect(snapshotCells([{ employeeId: 1, date: '2026-01-01' }], () => null))
      .toEqual([{ employeeId: 1, date: '2026-01-01', scheduled: false, overridden: false, leave: false, half_rest: false, shift_id: null, zone_id: null }])
  })

  it('逐格还原计划：原本没覆盖的走删覆盖，原本有覆盖的写回原值', () => {
    const plan = undoPlan(snapshotCells(cells, cellOf), [])
    expect(plan).toEqual([
      { employeeId: 7, date: '2026-10-12', kind: 'clear' },
      { employeeId: 9, date: '2026-10-12', kind: 'restore', payload: { shift_id: 4, zone_id: 6 } },
      // 批出来的假不是这两条接口写得回去的：如实说不还原，不拿一个「休」盖掉它
      { employeeId: 11, date: '2026-10-13', kind: 'keep' },
    ])
  })

  it('原本有覆盖、那天是「休」的格子：写回 is_rest', () => {
    const snapshot = [{ employeeId: 7, date: '2026-10-12', overridden: true, leave: false, shift_id: null, zone_id: null }]
    expect(undoPlan(snapshot, [])).toEqual([
      { employeeId: 7, date: '2026-10-12', kind: 'restore', payload: { is_rest: true } },
    ])
  })

  it('撤销「休半天」：按原值写回 half_rest，不拿「写回原班次」那条路盖上去', () => {
    // 那一笔只动 half_rest（班次 / 工作区原样），所以计划项是 `half_rest` 而不是 clear / restore；
    // 否则写回班次时后端不会把半天休改回来，撤销等于没撤。
    const snapshot = snapshotCells(cells, cellOf)
    expect(undoPlan(snapshot, [], PICK_HALF_REST)).toEqual([
      { employeeId: 7, date: '2026-10-12', kind: 'half_rest', payload: { half_rest: false } },
      { employeeId: 9, date: '2026-10-12', kind: 'half_rest', payload: { half_rest: true } },
      { employeeId: 11, date: '2026-10-13', kind: 'half_rest', payload: { half_rest: false } },
    ])
    // 服务端跳过（那一格没班次 / 没被写过）的格子不进计划。
    expect(undoPlan(snapshot, [
      { employeeId: 7, date: '2026-10-12', reason: 'half_rest_needs_shift' },
      { employeeId: 11, date: '2026-10-13', reason: 'leave_approved' },
    ], PICK_HALF_REST)).toEqual([
      { employeeId: 9, date: '2026-10-12', kind: 'half_rest', payload: { half_rest: true } },
    ])
  })

  it('服务端跳过的格子不进计划：它们没被写过，还原反而会改坏', () => {
    const snapshot = snapshotCells(cells, cellOf)
    const plan = undoPlan(snapshot, [
      { employeeId: 7, date: '2026-10-12', reason: 'leave_approved' },
      { employeeId: 11, date: '2026-10-13', reason: 'past_day' },
    ])
    expect(plan).toEqual([
      { employeeId: 9, date: '2026-10-12', kind: 'restore', payload: { shift_id: 4, zone_id: 6 } },
    ])
    expect(undoPlan([], [])).toEqual([])
  })
})

// 表头 / 人名列的高亮判据（`.gW-th.on`）：只有**整列 / 整行被完整覆盖**才亮。
// 用「覆盖到就亮」的话，选一整列会让 19 个人名一起描框 —— 比选区本身还抢眼。
describe('周表选区：整轴被完整覆盖才算选到那一行 / 那一列', () => {
  it('选一整列：那一个列头亮，行头一个都不亮', () => {
    const region = pickRegion(pickAt(null, colHandle('2026-10-13')), AXES)
    expect(region.count).toBe(3)
    expect(region.fullCols).toEqual(['2026-10-13'])
    expect(region.fullRows).toEqual([])
  })

  it('选一整行：那一个行头亮，列头一个都不亮', () => {
    const region = pickRegion(pickAt(null, rowHandle(9)), AXES)
    expect(region.fullRows).toEqual([9])
    expect(region.fullCols).toEqual([])
  })

  it('连着两列 / 连着两行：覆盖到的那两个头都亮（没铺满的那根轴不亮）', () => {
    const cols = pickRegion(twoPoints(colHandle('2026-10-12'), colHandle('2026-10-13')), AXES)
    expect(cols.fullCols).toEqual(['2026-10-12', '2026-10-13'])
    expect(cols.fullRows).toEqual([])
    const rows = pickRegion(twoPoints(rowHandle(7), rowHandle(9)), AXES)
    expect(rows.fullRows).toEqual([7, 9])
    expect(rows.fullCols).toEqual([])
  })

  it('局部矩形：一根轴都没铺满，只有格子亮', () => {
    // 行 7→9 是这张 3 行表里的**前两行**，列 12→13 是前两列 → 2 × 2 = 4 格。
    const rect = pickRegion(twoPoints(cellHandle(7, '2026-10-12'), cellHandle(9, '2026-10-13')), AXES)
    expect(rect.count).toBe(4)
    expect([rect.fullRows, rect.fullCols]).toEqual([[], []])
  })

  it('一个格子：两轴都不完整（点一格不该点亮它那一列的头）', () => {
    const one = pickRegion(pickAt(null, cellHandle(9, '2026-10-13')), AXES)
    expect([one.fullRows, one.fullCols]).toEqual([[], []])
  })

  it('矩形正好铺满整轴（选满整张表）时，两轴都算完整', () => {
    const all = pickRegion(twoPoints(cellHandle(7, '2026-10-12'), cellHandle(11, '2026-10-14')), AXES)
    expect(all.count).toBe(9)
    expect(all.fullRows).toEqual(AXES.rows)
    expect(all.fullCols).toEqual(AXES.cols)
  })
})
