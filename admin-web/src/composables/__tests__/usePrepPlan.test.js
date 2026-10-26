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
  entries: [
    {
      id: 1,
      prep_item_id: 11,
      item_name: '虾饺馅',
      unit: '份',
      station: 'shulong',
      kind: 'made',
      suggested_qty: 80,
      final_qty: 80,
      qty_source: 'suggested',
      done_at: null,
      history7: [],
    },
    {
      id: 2,
      prep_item_id: 12,
      item_name: '河粉',
      unit: '斤',
      station: 'shulong',
      kind: 'bought',
      suggested_qty: 20,
      final_qty: 20,
      qty_source: 'suggested',
      done_at: null,
      history7: [],
    },
    {
      id: 3,
      prep_item_id: 13,
      item_name: '蛋挞盏',
      unit: '个',
      station: 'xibing',
      kind: 'made',
      suggested_qty: 30,
      final_qty: 30,
      qty_source: 'suggested',
      done_at: '2026-10-22T09:00:00+08:00',
      history7: [],
    },
  ],
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.get.mockReset()
  api.post.mockReset()
  api.patch.mockReset()
  api.get.mockImplementation(async (path) => {
    if (path === '/api/stations') {
      return [
        { id: 'shulong', name: '熟笼' },
        { id: 'xibing', name: '西饼' },
      ]
    }
    if (path === '/api/prep-plan/today') return TODAY
    return {}
  })
})

describe('档口页签与两段分组', () => {
  it('按档口分组、带剩余计数，默认落在第一个还有待办项的档口', async () => {
    const plan = usePrepPlan()
    await plan.load()

    expect(plan.state.value).toBe('ready')
    expect(plan.stationTabs.value.map((tab) => [tab.id, tab.remaining])).toEqual([
      ['shulong', 2],
      ['xibing', 0],
    ])
    expect(plan.activeStation.value).toBe('shulong')
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
    plan.activeStation.value = 'xibing'

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
