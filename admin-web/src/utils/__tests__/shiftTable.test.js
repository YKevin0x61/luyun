import { describe, expect, it } from 'vitest'
import {
  DUTY_SLOT_CHOICES,
  canDelete,
  deleteBlockedReason,
  dutySlotPayload,
  dutySlotText,
  dutySlotValue,
  mergeShiftList,
  moveShift,
  statusText,
  toggleLabel,
  usageLine,
} from '../shiftTable'

// 服务端 `/shifts/manage` 里的一条（票 11）：`people` 决定能不能停用，`days` 决定能不能删。
const SHIFT = { id: 1, name: '白班', sort_order: 10, is_active: true, people: 2, days: 90 }

describe('班次表那一行（票 11）', () => {
  it('用量把两件事分开说：几个人的轮转里排着它、已经排过多少天班', () => {
    expect(usageLine(SHIFT)).toBe('2 个人的轮转里排着它 · 已经排过 90 天班')
    expect(usageLine({ ...SHIFT, people: 1, days: 0 })).toBe('1 个人的轮转里排着它 · 还没排过班')
    expect(usageLine({ ...SHIFT, people: 0, days: 0 })).toBe('没有人的轮转里排着它 · 还没排过班')
    expect(usageLine(null)).toBe('')
  })

  it('在用 / 已停用，按钮说的是下一步该做什么', () => {
    expect(statusText(SHIFT)).toBe('在用')
    expect(statusText({ ...SHIFT, is_active: false })).toBe('已停用')
    expect(toggleLabel(SHIFT)).toBe('停用')
    expect(toggleLabel({ ...SHIFT, is_active: false })).toBe('启用')
    expect(toggleLabel(null)).toBe('启用')
  })
})

describe('删不删得掉（票 11 验收 5）', () => {
  it('「一天班都没排过、也没人在用」才给删；最后一个在用的也不给删', () => {
    expect(canDelete({ ...SHIFT, people: 0, days: 0 }, 2)).toBe(true)
    // 排过班的删不掉：那些天在月历上会找不到班次。
    expect(canDelete({ ...SHIFT, people: 0 }, 2)).toBe(false)
    // 只在别人轮转里排着、还没排过班的也删不掉：规则一展开就会写回来。
    expect(canDelete({ ...SHIFT, days: 0 }, 2)).toBe(false)
    expect(canDelete(SHIFT, 2)).toBe(false)
    // 停用的那条删得掉（删它不影响「还有没有人能排班」），在用的最后一条不行 ——
    // 服务端是同一个口径（`last_active_shift`）。
    expect(canDelete({ ...SHIFT, people: 0, days: 0, is_active: false }, 1)).toBe(true)
    expect(canDelete({ ...SHIFT, people: 0, days: 0 }, 1)).toBe(false)
    // 拿不到 `active_count` 时按最保守的算：按钮灰着，别让店长点了才挨拒。
    expect(canDelete({ ...SHIFT, people: 0, days: 0 })).toBe(false)
    expect(canDelete(null, 2)).toBe(false)
  })

  it('删不掉时把理由说全 —— 不是只把按钮变灰', () => {
    expect(deleteBlockedReason(SHIFT, 2)).toBe(
      '删不掉（已经排过 90 天班，还有 2 个人的轮转里排着它）：停用就够了，历史排班照旧显示',
    )
    expect(deleteBlockedReason({ ...SHIFT, people: 0 }, 2)).toBe(
      '删不掉（已经排过 90 天班）：停用就够了，历史排班照旧显示',
    )
    expect(deleteBlockedReason({ ...SHIFT, days: 0 }, 2)).toBe(
      '删不掉（还有 2 个人的轮转里排着它）：停用就够了，历史排班照旧显示',
    )
    // 最后一个在用的：说的是另一件事（加一条、或启用一条别的）。
    expect(deleteBlockedReason({ ...SHIFT, people: 0, days: 0 }, 1)).toBe(
      '删不掉（这是最后一个在用的班次）：先加一个或启用一个别的班次',
    )
    // 能删的时候没有理由可写（页面也就不显示那一行）。
    expect(deleteBlockedReason({ ...SHIFT, people: 0, days: 0 }, 2)).toBe('')
    expect(deleteBlockedReason(null, 2)).toBe('')
  })
})

describe('上移 / 下移（票 11 验收 1）', () => {
  const ids = [1, 2, 3]

  it('只换这两个人的位置，返回整表的新顺序（服务端要一次给全）', () => {
    expect(moveShift(ids, 1, -1)).toEqual([2, 1, 3])
    expect(moveShift(ids, 1, 1)).toEqual([1, 3, 2])
    expect(moveShift(ids, 0, 1)).toEqual([2, 1, 3])
    // 不改传进来的数组：页面上的列表要等 PUT 成功、重读之后才变。
    expect(ids).toEqual([1, 2, 3])
  })

  it('到顶 / 到底 / 位置不对都不给动（按钮该是灰的）', () => {
    expect(moveShift(ids, 0, -1)).toBeNull()
    expect(moveShift(ids, 2, 1)).toBeNull()
    expect(moveShift(ids, -1, 1)).toBeNull()
    expect(moveShift(ids, 3, 1)).toBeNull()
    expect(moveShift([], 0, 1)).toBeNull()
    expect(moveShift(null, 0, 1)).toBeNull()
  })
})

describe('卫生档位（票 10）：白班档 / 夜班档 / 不出日常', () => {
  it('三种值各自的人话，缺字段和认不出的值都落成「不出日常」', () => {
    expect(dutySlotText({ ...SHIFT, duty_slot: 'day' })).toBe('白班档')
    expect(dutySlotText({ ...SHIFT, duty_slot: 'night' })).toBe('夜班档')
    expect(dutySlotText({ ...SHIFT, duty_slot: null })).toBe('不出日常')
    expect(dutySlotText({ ...SHIFT, duty_slot: '' })).toBe('不出日常')
    // 缺字段（还没应用 0010 的库）与认不出的值都不猜一档：猜错会让卫生那边凭空
    // 多出一份日常检查，而「不出日常」只是没人交，页面会说去找店长。
    expect(dutySlotText({ id: 9, name: '中班', is_active: true })).toBe('不出日常')
    expect(dutySlotText({ ...SHIFT, duty_slot: 'evening' })).toBe('不出日常')
    expect(dutySlotText(null)).toBe('不出日常')
  })

  it('下拉里选中的那个值就是现在的档位（认不出的并成第三条）', () => {
    expect(dutySlotValue({ ...SHIFT, duty_slot: 'day' })).toBe('day')
    expect(dutySlotValue({ ...SHIFT, duty_slot: 'night' })).toBe('night')
    expect(dutySlotValue({ ...SHIFT, duty_slot: null })).toBe('')
    expect(dutySlotValue({ ...SHIFT, duty_slot: 'evening' })).toBe('')
    expect(dutySlotValue({ id: 9 })).toBe('')
    expect(dutySlotValue(null)).toBe('')
  })

  it('三选一：值就是 PUT 要发的三种，文案是页面上的那三句', () => {
    expect(DUTY_SLOT_CHOICES.map((choice) => choice.value)).toEqual(['day', 'night', ''])
    expect(DUTY_SLOT_CHOICES.map((choice) => choice.label)).toEqual(['白班档', '夜班档', '不出日常'])
  })

  it('提交的那一项：清空发的是空串，**不是 null**（null = 这一项不动）', () => {
    expect(dutySlotPayload('day')).toBe('day')
    expect(dutySlotPayload('night')).toBe('night')
    // PUT 的语义：`null`（或不给）= 不动、空串 = 清空。选了「不出日常」发 `null`，
    // 后端当没改 —— 页面已经显示成清空了，店长以为改好了，卫生照旧发检查单。
    expect(dutySlotPayload('')).toBe('')
    expect(dutySlotPayload('')).not.toBeNull()
    expect(dutySlotPayload(null)).toBe('')
    expect(dutySlotPayload(undefined)).toBe('')
    // 界面上只有三个选项，别的值只可能是自己传错：按「不出日常」发，不猜一档。
    expect(dutySlotPayload('evening')).toBe('')
  })
})

describe('两份班次列表合成一份（月历的班次列）', () => {
  const DAY = { id: 1, name: '白班', is_active: true }
  const NIGHT = { id: 2, name: '夜班', is_active: false }
  const MIDDLE = { id: 3, name: '中班', is_active: true }

  it('以显示那份的顺序为准，另一份里没见过的接在后面', () => {
    expect(mergeShiftList([DAY, NIGHT], [DAY, MIDDLE]).map((shift) => shift.id)).toEqual([1, 2, 3])
  })

  it('停用的班次不会被「只有启用班次」的那份挤掉', () => {
    // `/calendar` 那份 = 启用的全要 + 这个月真有行的停用班次；`/roster` 那份只有启用的。
    // 直接替换，停用班次就从格子、图例、当天卡的颜色里消失 —— 而它在这个月明明还有行
    // （票 11 验收②：停用之后历史排班照旧显示）。
    expect(mergeShiftList([NIGHT], [DAY]).map((shift) => shift.name)).toEqual(['夜班', '白班'])
  })

  it('缺一份也照常出另一份（首屏、名单没读出来时不至于空成一片）', () => {
    expect(mergeShiftList([], [DAY]).map((shift) => shift.id)).toEqual([1])
    expect(mergeShiftList([DAY], []).map((shift) => shift.id)).toEqual([1])
    expect(mergeShiftList(null, null)).toEqual([])
    expect(mergeShiftList(undefined, [DAY]).map((shift) => shift.id)).toEqual([1])
  })

  it('不改传进来的数组：页面上那份要等重读之后才变', () => {
    const shown = [DAY]
    mergeShiftList(shown, [MIDDLE])
    expect(shown.map((shift) => shift.id)).toEqual([1])
  })
})
