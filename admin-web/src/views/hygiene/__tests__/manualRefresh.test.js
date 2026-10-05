// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'

import { FALLBACK_GRACE_MS } from '../../../composables/useConnectionFallback'

/**
 * 票 06 · 员工端手动刷新要覆盖「待我验收」那一段。
 *
 * `refreshAll()` 的写法是"不挑 resource，全部重拉"：它把 ms/日常/专项/整改/工作区名单
 * **逐条直接**列出来（`loadMe` 里其实也拉那四条，重复是故意的 —— 断连时 `/me` 自己会失败
 * 并进退避重试，别的几段不该跟着它一起哑掉）。这份名单里漏了 `loadPendingReviews`：
 * 只开「日常验收」的人点刷新，日常/专项/整改/工作区都换了新数据，「待我验收」却还挂在
 * `/me` 那一条链路上 —— `/me` 一失败它就停在上一次的样子，而界面上没有任何地方提示他
 * "这一段没刷"（`.scratch/admin-caps/decisions/02-roster-nudge-reaches-staff.md` 记的第三个口子）。
 *
 * 断言的都是外部行为：刷新这个动作**触发了哪几条读取**、以及列表**是不是新读的**
 * （服务端这次给了别的内容，页面上要跟着变）—— 不断言它内部怎么调。
 *
 * 手动刷新的入口在员工页上只有一处：断连横幅上那颗「刷新」（`@click="refreshAll"`）。
 * 走到它得先让断开状态持续过宽限期（避免瞬时抖动闪一下），所以这一条用假时钟把宽限期
 * 走完 —— 顺带把横幅自己的显示时机也钉住：宽限期内不出现、过了才出现。
 */

const ZONE = { id: 3, name: '案板' }

function employee(caps) {
  return {
    id: 5,
    name: '余威威',
    phone: '13800000000',
    job_title: '案板',
    permission: caps.length ? '管理员' : '普通员工',
    admin_caps: caps,
    shift: '白班',
    zone_id: 3,
    zone_name: '案板',
    zone_shifts: ['白班', '夜班'],
  }
}

function reviewRow(itemId, name) {
  return {
    item_id: itemId,
    name,
    zone_id: ZONE.id,
    zone_name: ZONE.name,
    shift: '白班',
    status: '待验收',
    submitter_id: 99,
  }
}

/** 服务端此刻的「待我验收」内容：刷新前后改它，页面必须跟着变。 */
let pendingReviews = []
/** 刷新那一刻 `/me` 通不通。横幅说的正是"和服务器断了"这种时候，它对员工是同一个按钮。 */
let meFails = false

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 假时钟下 `flushPromises`（内部走 setTimeout）不会自己跑：推进 0ms 把微任务排空。 */
async function settle() {
  await vi.advanceTimersByTimeAsync(0)
  await vi.advanceTimersByTimeAsync(0)
}

async function mountHome(caps) {
  const requested = []
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    // 记的是**带 query 的原地址**：`?review=1`（「待我验收」那一档）重拉没有要看得到。
    const raw = String(url)
    requested.push(raw)
    const [path, query = ''] = raw.split('?')
    if (path === '/api/hygiene/staff/me') {
      if (meFails) return jsonResponse({ detail: '服务器开小差' }, 503)
      return jsonResponse({
        employee: employee(caps),
        daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
        deep_clock: { hhmm: '20:00' },
      })
    }
    if (path === '/api/hygiene/staff/daily-work') {
      // 不带 query 的是他自己的日常队列；`?review=1` 才是「待我验收」那一档。
      return jsonResponse({ items: query.includes('review=1') ? pendingReviews : [] })
    }
    if (path === '/api/hygiene/staff/deep-clean') return jsonResponse({ items: [], status: '待办' })
    if (path === '/api/hygiene/staff/fix') return jsonResponse({ items: [] })
    if (path === '/api/hygiene/staff/daily-catalog') return jsonResponse({ zones: [ZONE] })
    if (path === '/api/hygiene/staff/boards') return jsonResponse({ week_start: '', people: [], zones: [] })
    if (path === '/api/hygiene/staff/teaching') return jsonResponse({ items: [] })
    return jsonResponse({ detail: 'not found' }, 404)
  }))

  const { default: HygieneHomeView } = await import('../HygieneHomeView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/me/clean', component: { template: '<div />' } }],
  })
  await router.push('/workbench/me/clean')
  await router.isReady()

  vi.useFakeTimers()
  const wrapper = mount(HygieneHomeView, {
    global: {
      plugins: [router, pinia],
      provide: { wsConnected: ref(false) },
      stubs: { StandardPhotoCachePanel: true },
    },
  })
  await settle()
  const offlineBannerBeforeGrace = wrapper.find('.hy-staff-offline').exists()
  await vi.advanceTimersByTimeAsync(FALLBACK_GRACE_MS)
  await settle()
  // 宽限期之后按真时钟走：fetch 与 flushPromises 都不靠定时器。
  vi.useRealTimers()
  await flushPromises()
  return { wrapper, requested, offlineBannerBeforeGrace }
}

/** 「待我验收」那一档被读了几次（只有它是带 `?review=1` 的请求）。 */
function reviewCalls(requested) {
  return requested.filter((url) => url.includes('review=1')).length
}

async function clickRefresh(wrapper) {
  const button = wrapper.findAll('button').find((node) => node.text() === '刷新')
  expect(button, '断连横幅上那颗手动刷新按钮').toBeTruthy()
  // 用原生 click 而不是 VTU 的 `trigger`：`trigger` 会给事件写上 `_vts`，Vue 拿它跟
  // 「监听器挂载时刻」比大小、早于它的直接丢掉 —— 而这颗按钮是在假时钟（已推进 8 秒）下
  // 挂上的，按真实墙钟打的时间戳反而更早。原生 click 不带 `_vts`，走的就是用户点它的那条路。
  button.element.click()
  await flushPromises()
  await flushPromises()
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  pendingReviews = []
  meFails = false
})

describe('员工卫生页 · 手动刷新覆盖「待我验收」', () => {
  it('刷新那一刻 /me 不通：这一档照样重拉，不跟着 /me 一起停在旧数据上', async () => {
    pendingReviews = [reviewRow(1, '台面')]
    const { wrapper, requested } = await mountHome(['daily_review'])
    const before = reviewCalls(requested)
    expect(before).toBeGreaterThan(0)

    // 断连那段时间别人交了活；`/me` 这一刻不返回（页面对它自己有退避重试）。
    pendingReviews = [reviewRow(1, '台面'), reviewRow(2, '灶台')]
    meFails = true
    await clickRefresh(wrapper)

    expect(reviewCalls(requested)).toBeGreaterThan(before)
    expect(wrapper.text()).toContain('灶台')
    wrapper.unmount()
  })

  it('只开「日常验收」的人点刷新：列表是新读的那一份（条数跟着变）', async () => {
    pendingReviews = [reviewRow(1, '台面')]
    const { wrapper } = await mountHome(['daily_review'])
    expect(wrapper.text()).toContain('台面')
    expect(wrapper.text()).toContain('待我验收 1')

    pendingReviews = [reviewRow(1, '台面'), reviewRow(2, '灶台')]
    await clickRefresh(wrapper)

    expect(wrapper.text()).toContain('灶台')
    expect(wrapper.text()).toContain('待我验收 2')
    wrapper.unmount()
  })

  it('没开「日常验收」的人点刷新：不发这条请求（那一档本来就没有，服务端对普通员工是 403）', async () => {
    const { wrapper, requested } = await mountHome(['fix'])
    const before = requested.length

    await clickRefresh(wrapper)

    // 刷新真的跑了（别的几条读取照旧）。
    expect(requested.length).toBeGreaterThan(before)
    expect(requested.some((url) => url.includes('review=1'))).toBe(false)
    wrapper.unmount()
  })

  it('断连横幅那颗刷新按钮行为不变：宽限期内不出现，点了照旧把原来那五条全部重拉', async () => {
    const { wrapper, requested, offlineBannerBeforeGrace } = await mountHome(['daily_review'])
    // 横幅自己的显示时机：瞬时抖动不闪，断开超过宽限期才出现；手动刷新入口就是它那颗按钮。
    expect(offlineBannerBeforeGrace).toBe(false)
    expect(wrapper.find('.hy-staff-offline').exists()).toBe(true)

    const before = requested.length
    await clickRefresh(wrapper)
    const fired = requested.slice(before)

    for (const path of [
      '/api/hygiene/staff/me',
      '/api/hygiene/staff/daily-work',
      '/api/hygiene/staff/deep-clean',
      '/api/hygiene/staff/fix',
      '/api/hygiene/staff/daily-catalog',
    ]) {
      expect(fired.some((url) => url.startsWith(path)), `${path} 还在刷新名单里`).toBe(true)
    }
    wrapper.unmount()
  })
})
