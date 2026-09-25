import { describe, expect, it } from 'vitest'
import {
  canDelete,
  deleteBlockedReason,
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
