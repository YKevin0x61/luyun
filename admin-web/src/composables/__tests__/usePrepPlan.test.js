// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

const { api } = await import('../../api/client')
const { usePrepPlan } = await import('../usePrepPlan.js')

const TODAY = {
  plan_day: '2026-10-22',
  window: { start: '2026-10-22T08:00:00+08:00', end: '2026-10-22T21:30:00+08:00', closed: false },
  staff: false,
  staff_station: null,
  needs_station_pick: false,
  remaining_count: 2,
  stations: [
    { id: '熟笼', label: '熟笼' },
    { id: '西饼', label: '西饼' },
  ],
  entries: [
    {
      id: 1,
      prep_item_id: 11,
      item_name: '虾饺馅',
      unit: '份',
      station: 'shulong',
      position: '熟笼',
      kind: 'made',
      suggested_qty: 80,
      final_qty: 80,
      qty_source: 'suggested',
      done_at: null,
      history: [],
    },
    {
      id: 2,
      prep_item_id: 12,
      item_name: '河粉',
      unit: '斤',
      station: 'shulong',
      position: '熟笼',
      kind: 'bought',
      suggested_qty: 20,
      final_qty: 20,
      qty_source: 'suggested',
      done_at: null,
      history: [],
    },
    {
      id: 3,
      prep_item_id: 13,
      item_name: '蛋挞盏',
      unit: '个',
      station: 'xibing',
      position: '西饼',
      kind: 'made',
      suggested_qty: 30,
      final_qty: 30,
      qty_source: 'suggested',
      done_at: '2026-10-22T09:00:00+08:00',
      history: [],
    },
  ],
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.get.mockReset()
  api.post.mockReset()
  api.patch.mockReset()
  api.get.mockImplementation(async (path) => {
    if (path === '/api/prep-plan/today') return TODAY
    return {}
  })
})

describe('岗位页签与两段分组', () => {
  it('按岗位分组、带剩余计数，默认落在第一个还有待办项岗位', async () => {
    const plan = usePrepPlan()
    await plan.load()

    expect(plan.state.value).toBe('ready')
    expect(plan.stationTabs.value.map((tab) => [tab.id, tab.remaining])).toEqual([
      ['熟笼', 2],
      ['西饼', 0],
    ])
    expect(plan.activeStation.value).toBe('熟笼')
  })

  it('档口内分成「半成品」与「原料」两段', async () => {
    const plan = usePrepPlan()
    await plan.load()

    expect(
      plan.groups.value.map((group) => [group.kind, group.entries.map((row) => row.item_name)]),
    ).toEqual([
      ['made', ['虾饺馅']],
      ['bought', ['河粉']],
    ])
  })

  it('勾掉的进已做，不进待办', async () => {
    const plan = usePrepPlan()
    await plan.load()
    plan.activeStation.value = '西饼'

    expect(plan.groups.value).toEqual([])
    expect(plan.doneInStation.value.map((row) => row.item_name)).toEqual(['蛋挞盏'])
  })
})

describe('写动作', () => {
  it('勾选发 PATCH done:true，并把这一行标成已做', async () => {
    api.patch.mockResolvedValue({ success: true })
    const plan = usePrepPlan()
    await plan.load()
    const entry = plan.groups.value[0].entries[0]

    await plan.toggleDone(entry)

    expect(api.patch).toHaveBeenCalledWith('/api/prep-plan/today/entries/1', { done: true })
    expect(plan.remainingCount.value).toBe(1)
  })

  it('改数量发 PATCH final_qty，并把来源标成 manual', async () => {
    api.patch.mockResolvedValue({ success: true })
    const plan = usePrepPlan()
    await plan.load()
    const entry = plan.groups.value[0].entries[0]

    await plan.setQty(entry, 95)

    expect(api.patch).toHaveBeenCalledWith('/api/prep-plan/today/entries/1', { final_qty: 95 })
    const updated = plan.entries.value.find((row) => row.id === 1)
    expect(updated.final_qty).toBe(95)
    expect(updated.qty_source).toBe('manual')
  })

  it('非法数量不发请求', async () => {
    const plan = usePrepPlan()
    await plan.load()
    const entry = plan.groups.value[0].entries[0]

    await plan.setQty(entry, 'abc')
    expect(api.patch).not.toHaveBeenCalled()
  })
})


describe('时间跨度', () => {
  beforeEach(() => {
    // 上一个用例可能刚把跨度存进去；这里的每条都想从干净的默认值开始
    window.localStorage.clear()
  })

  it('load 带上当前跨度，后端回传的值覆盖本地状态', async () => {
    api.get.mockImplementation(async (path, params) => {
      if (path === '/api/prep-plan/today') {
        return { ...TODAY, window: { ...TODAY.window, horizon_hours: params.hours } }
      }
      return {}
    })
    const plan = usePrepPlan()
    await plan.load()
    expect(api.get).toHaveBeenCalledWith('/api/prep-plan/today', { hours: 24 })

    await plan.setHorizon(72)
    expect(api.get).toHaveBeenLastCalledWith('/api/prep-plan/today', { hours: 72 })
    expect(plan.horizonHours.value).toBe(72)
    expect(window.localStorage.getItem('prepPlanHorizon')).toBe('72')
  })

  it('不认的值不切、也不落盘', async () => {
    const plan = usePrepPlan()
    await plan.load()
    await plan.setHorizon(30)
    expect(plan.horizonHours.value).toBe(24)
    expect(window.localStorage.getItem('prepPlanHorizon')).toBeNull()
  })

  it('存了个坏值就回落 24 小时', async () => {
    window.localStorage.setItem('prepPlanHorizon', 'abc')
    const plan = usePrepPlan()
    expect(plan.horizonHours.value).toBe(24)
    await plan.load()
  })
})
