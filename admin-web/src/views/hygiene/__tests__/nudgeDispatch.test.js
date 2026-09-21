import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

const home = read('../HygieneHomeView.vue')

describe('nudge 只拉起当前真正要用的数据', () => {
  it('only follows boards/teaching while the boards tab is on screen', () => {
    // 服务端一次提交会广播 daily + boards 两条；不在「榜」那一屏就没必要跟着拉，
    // 切到该 tab 时 watch(tab) 本来就会强制拉一次。
    expect(home).toMatch(/resource === 'boards' && tab\.value === 'boards'/)
    expect(home).toMatch(/resource === 'teaching' && tab\.value === 'boards'/)
    expect(home).not.toMatch(/if \(resource === 'boards'\) await loadBoards/)
  })

  it('does not re-pull zones for a staff member who already picked one', () => {
    // zones 这个 resource 现在分两种用途：选区列表变更，以及标准图换版/改标注
    // （action=standard_updated）。前者仍然只对「还没选区」的员工拉列表，
    // 后者只刷待办——见 standardMarkupEdit.test.js。
    const branch = home.slice(home.indexOf("if (resource === 'zones')"))
    expect(branch).toMatch(/if \(!employee\.value\.zone_id\) await loadZones\(\{ force: true \}\)/)
    expect(home).not.toMatch(/if \(resource === 'zones'\) await loadZones/)
    expect(home).not.toMatch(/if \(resource === 'zones'\) await refreshPage/)
  })

  it('still refreshes the daily inbox, which feeds the tab badge', () => {
    expect(home).toMatch(/if \(resource === 'daily'\) await loadInbox\(\)/)
    expect(home).toMatch(/if \(resource === 'fix'\) await loadFixTickets\(\)/)
  })
})

describe('useHygieneRealtime 透传 filters', () => {
  const mocks = vi.hoisted(() => ({ useNudgePull: vi.fn(() => ({})) }))

  beforeEach(() => {
    vi.resetModules()
    mocks.useNudgePull.mockClear()
  })

  it('passes filters through so the hub can drop irrelevant nudges', async () => {
    vi.doMock('../../../composables/useNudgePull', () => ({ useNudgePull: mocks.useNudgePull }))
    const { useHygieneRealtime } = await import('../../../composables/useHygieneRealtime')

    useHygieneRealtime({
      id: 'x',
      resources: ['daily'],
      pull: () => {},
      filters: { employee_id: 7 },
    })
    expect(mocks.useNudgePull).toHaveBeenCalledTimes(1)
    expect(mocks.useNudgePull.mock.calls[0][0]).toMatchObject({
      topics: ['hygiene'],
      filters: { employee_id: 7 },
    })
  })

  it('defaults to no filters, keeping today behaviour', async () => {
    vi.doMock('../../../composables/useNudgePull', () => ({ useNudgePull: mocks.useNudgePull }))
    const { useHygieneRealtime } = await import('../../../composables/useHygieneRealtime')

    useHygieneRealtime({ id: 'y', resources: ['daily'], pull: () => {} })
    expect(mocks.useNudgePull.mock.calls[0][0]).toMatchObject({ filters: {} })
  })
})
