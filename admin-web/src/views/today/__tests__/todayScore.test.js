// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 「我的成绩」卡（2026-10-05 用户裁定）。
 *
 * 这张卡最要紧的一条判据是**空态**：`pass_rate === null` 表示"这段时间还没交过活"，
 * 界面上必须与 0%（交了但都被驳）分开 —— 给新人看 0%，他会当成已经被扣分。所以下面
 * 既有 null 那条，也有 0 那条，两边一起压。
 */

const ME = {
  employee: { id: 7, name: '余威威' },
  days: [],
}

const HYGIENE_ME = {
  employee: { id: 7, name: '余威威', phone: '13800000000', shift: '白班', zone_id: 3, zone_name: '案板' },
  daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
  deep_clock: { hhmm: '20:00' },
}

const STATS = {
  days: 7,
  since: '2026-09-29',
  business_date: '2026-10-05',
  first_pass: 12,
  rejected: 2,
  captured: 14,
  missed: 0,
  pass_rate: 0.857,
  reasons: [
    { reason: '台面还有油渍', count: 2 },
    { reason: '角落没擦到', count: 1 },
  ],
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountToday({ stats = STATS, statsStatus = 200 } = {}) {
  const statsUrls = []
  const fetchMock = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    if (method !== 'GET') return jsonResponse({ ok: true })
    if (path === '/api/scheduling/me') return jsonResponse(ME)
    if (path === '/api/scheduling/me/requests') return jsonResponse({ requests: [], incoming: [] })
    if (path === '/api/hygiene/staff/me') return jsonResponse(HYGIENE_ME)
    if (path === '/api/hygiene/staff/daily-work') return jsonResponse({ items: [] })
    if (path === '/api/hygiene/staff/attire') return jsonResponse({ attire: { required: false } })
    if (path === '/api/hygiene/staff/me/stats') {
      statsUrls.push(raw)
      if (statsStatus !== 200) return jsonResponse({ detail: '统计读不出来' }, statsStatus)
      return jsonResponse(stats)
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const { default: TodayView } = await import('../TodayView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/me/today', component: { template: '<div />' } }],
  })
  await router.push('/workbench/me/today')
  await router.isReady()

  const wrapper = mount(TodayView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, statsUrls }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('「我的成绩」卡：近 7 天的一次通过率', () => {
  it('主数字是一次通过率，旁边是一次通过与驳回次数，窗口固定问 days=7', async () => {
    const { wrapper, statsUrls } = await mountToday()
    const card = wrapper.get('.tA-card.score')

    expect(card.get('.score-rate').text()).toBe('86%')
    const split = card.get('.score-split').text()
    expect(split).toContain('一次通过')
    expect(split).toContain('12 项')
    expect(split).toContain('被驳回')
    expect(split).toContain('2 次')
    expect(card.text()).toContain('近 7 天')
    // 窗口是这一条请求的 query（不是页面上写死的两个字）。
    expect(statsUrls).toEqual(['/api/hygiene/staff/me/stats?days=7'])
  })

  it('驳回原因一行一条带次数；一条都没有时整块不出现', async () => {
    const { wrapper } = await mountToday()
    const lines = wrapper.get('.tA-card.score').findAll('.score-reasons li')
    expect(lines.map((li) => li.text())).toEqual(['台面还有油渍2 次', '角落没擦到1 次'])

    const { wrapper: clean } = await mountToday({
      stats: { ...STATS, reasons: [] },
    })
    expect(clean.find('.score-reasons').exists()).toBe(false)
  })

  it('pass_rate 为 null（还没交过活）时不给数字，更没有 0%', async () => {
    const { wrapper } = await mountToday({
      stats: { ...STATS, first_pass: 0, rejected: 0, captured: 0, pass_rate: null, reasons: [] },
    })
    const card = wrapper.get('.tA-card.score')

    // 空态里一个百分号都不许有：`0%` 与"还没开始"是两件事。
    expect(card.find('.score-rate').exists()).toBe(false)
    expect(card.text()).not.toContain('%')
    expect(card.get('.score-empty').text()).toContain('还没交过活')
    expect(card.text()).toContain('交一项就有记录')
  })

  it('pass_rate 为 0（交了但都被驳）时照样显示 0%', async () => {
    // 边界与上一条配对：真的 0 要显示，`null` 才不显示 —— 两条都不许被合并处理。
    const { wrapper } = await mountToday({
      stats: { ...STATS, first_pass: 0, rejected: 3, pass_rate: 0, reasons: [{ reason: '有水渍', count: 3 }] },
    })
    expect(wrapper.get('.tA-card.score').get('.score-rate').text()).toBe('0%')
  })

  it('读不出来只影响这张卡：说出话来 + 一个重试', async () => {
    const { wrapper } = await mountToday({ statsStatus: 500 })
    const card = wrapper.get('.tA-card.score')
    expect(card.text()).toContain('统计读不出来')
    expect(card.findAll('button').some((btn) => btn.text() === '重试')).toBe(true)
    // 上面那几张卡不受牵连。
    expect(wrapper.find('.tA-card.sched').exists()).toBe(true)
  })
})
