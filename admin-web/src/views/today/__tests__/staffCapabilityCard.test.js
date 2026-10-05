// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 「你能做的事」能力卡（票 01 / ADR 0093）。
 *
 * 病灶：被放权的员工在系统里看不见自己是谁 —— 能做什么、**还缺哪一项**都得靠撞墙试。
 * 这一块卡把十项开关里**员工端真有执行点的那三项**逐条摊开（开了的给说明，没开的写
 * 「店长还没开给你」），并说清这些事都在手机端「卫生」页里做。
 *
 * 这一条跟同目录 `todayAccount.test.js` 一样**真挂一遍**页，断言只压外部行为：
 * 渲染出了什么文案、哪几行、整块在不在。不碰内部结构，也不断言那七项未接线的键。
 * 先例：`hygiene/__tests__/rosterAdminCaps.test.js`（可勾的三项 + 只读的七项）、
 * `hygiene/__tests__/staffRoleTier.test.js`（同一页在**不同开关**下的差别）。
 */

const ME = {
  employee: { id: 7, name: '余威威' },
  days: [{
    business_date: '2026-10-06',
    is_today: true,
    scheduled: true,
    shift_id: 1,
    shift_name: '白班',
    zone_id: 3,
    zone_name: '案板',
    leave: false,
  }],
}

/** 服务端那一条 `/api/hygiene/staff/me`：`admin_caps` 是这一页唯一的权限判据。 */
function hygieneMe(caps, permission) {
  return {
    employee: {
      id: 7,
      name: '余威威',
      phone: '13800000000',
      job_title: '领班',
      permission,
      admin_caps: caps,
      shift: '白班',
      zone_id: 3,
      zone_name: '案板',
    },
    daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
    deep_clock: { hhmm: '20:00' },
  }
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountToday({ caps = [], permission = '普通员工' } = {}) {
  const requested = []
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    requested.push(path)
    if (path === '/api/scheduling/me') return jsonResponse(ME)
    if (path === '/api/scheduling/me/requests') return jsonResponse({ requests: [], incoming: [] })
    if (path === '/api/hygiene/staff/me') return jsonResponse(hygieneMe(caps, permission))
    if (path === '/api/hygiene/staff/daily-work') return jsonResponse({ items: [] })
    if (path === '/api/hygiene/staff/attire') return jsonResponse({ attire: { required: false } })
    if (path === '/api/hygiene/staff/me/stats') {
      return jsonResponse({ days: 7, pass_rate: null, first_pass: 0, rejected: 0, reasons: [] })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  }))

  const { default: TodayView } = await import('../TodayView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workbench/me/today', component: { template: '<div />' } },
      { path: '/workbench/me/clean', component: { template: '<div />' } },
    ],
  })
  await router.push('/workbench/me/today')
  await router.isReady()

  const wrapper = mount(TodayView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, router }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** 卡上某一项那一行（按钮/说明都在这一行里）。 */
function rowFor(wrapper, label) {
  const row = wrapper.findAll('.cap-row').find((li) => li.text().includes(label))
  if (!row) throw new Error(`卡上没有列出「${label}」`)
  return row
}

describe('「我的」页 · 你能做的事（能力卡）', () => {
  it('开了的能力逐条列出，每条带一句说明', async () => {
    const { wrapper } = await mountToday({ caps: ['daily_review', 'fix'], permission: '管理员' })
    const card = wrapper.get('.tA-card.caps')
    expect(card.text()).toContain('你能做的事')
    // 开了的那两项：名字 + 说明都在（说明是界面上给员工看的那句话，不是键名）。
    expect(card.text()).toContain('日常验收')
    expect(card.text()).toContain('判别人交的日常检查，看原图与标准图对照')
    expect(card.text()).toContain('整改单')
    expect(card.text()).toContain('开整改单、验收或驳回整改单')
    // 接口发下来的键名不露给员工看。
    expect(card.text()).not.toContain('daily_review')
    expect(card.text()).not.toContain('admin_caps')
  })

  it('没开通的能力也列出来，写明「店长还没开给你」—— 员工由此知道该找店长开什么', async () => {
    // 只开了日常验收：他要能看出「专项验收」不是系统不给，是店长还没开。
    const { wrapper } = await mountToday({ caps: ['daily_review'], permission: '管理员' })
    const card = wrapper.get('.tA-card.caps')

    expect(rowFor(wrapper, '专项验收').text()).toContain('店长还没开给你')
    expect(rowFor(wrapper, '整改单').text()).toContain('店长还没开给你')
    // 没开的那两项**不能**顺带把说明也摆出来（摆出来就等于说这项能用）。
    expect(rowFor(wrapper, '专项验收').text()).not.toContain('判专项卫生的前后对照')
    expect(rowFor(wrapper, '整改单').text()).not.toContain('开整改单、验收或驳回整改单')
    // 开了的那一项反过来：给的是说明，不是「还没开给你」。
    expect(rowFor(wrapper, '日常验收').text()).toContain('判别人交的日常检查，看原图与标准图对照')
    expect(rowFor(wrapper, '日常验收').text()).not.toContain('店长还没开给你')
    // 三项都在卡上（不是只列开了的那一项）。
    expect(card.text()).toContain('日常验收')
    expect(card.text()).toContain('专项验收')
    expect(card.text()).toContain('整改单')
  })

  it('完全没有权限的员工看不到这一块，页面其余部分照常', async () => {
    const { wrapper } = await mountToday({ caps: [], permission: '普通员工' })

    // 整块不在：连标题都不该留一个空壳。
    expect(wrapper.find('.tA-card.caps').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('你能做的事')
    expect(wrapper.text()).not.toContain('店长还没开给你')

    // 其余四块一块都不少：今天上不上班、今天的活、我的成绩、账号信息。
    expect(wrapper.get('.tA-card.sched').text()).toContain('白班')
    expect(wrapper.find('.tA-card.hyg').exists()).toBe(true)
    expect(wrapper.find('.tA-card.score').exists()).toBe(true)
    expect(wrapper.get('.me-meta').text()).toContain('余威威')
  })

  it('档位标签写着「管理员」但一项开关都没给：照样不渲染（判据是开关，不是标签）', async () => {
    // 库列与开关不一致是合法状态（ADR 0093）。界面给一个「管理员」的头衔、底下什么都做
    // 不了，正是这一票要治的毛病 —— 所以判据只看 `admin_caps`。
    const { wrapper } = await mountToday({ caps: [], permission: '管理员' })
    expect(wrapper.find('.tA-card.caps').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('你能做的事')
  })

  it('脚注说清这些事都在手机端「卫生」页里做，并给一个去那里的入口', async () => {
    const { wrapper, router } = await mountToday({ caps: ['daily_review'], permission: '管理员' })
    const card = wrapper.get('.tA-card.caps')
    expect(card.text()).toContain('这三项都在手机端「卫生」页里做')

    const go = card.findAll('button').find((btn) => btn.text().includes('卫生'))
    expect(go).toBeTruthy()
    await go.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/workbench/me/clean')
  })

  it('档位标签从账号信息里拿掉（信息由这张卡承载），姓名 / 区域 / 班次 / 职位照旧可读', async () => {
    const { wrapper } = await mountToday({ caps: ['daily_review'], permission: '管理员' })
    const card = wrapper.get('.tA-card.caps')
    const meta = wrapper.get('.me-meta')

    // 那张卡上带着档位（同一件事只说一遍的地方）。
    expect(card.text()).toContain('现场复核')
    // 账号信息回到纯资料：档位那一行没了。
    expect(meta.text()).not.toContain('卫生权限')
    expect(meta.text()).not.toContain('管理员')
    // 该读的还读得到。
    expect(meta.text()).toContain('余威威')
    expect(meta.text()).toContain('13800000000')
    expect(meta.text()).toContain('案板')
    expect(meta.text()).toContain('白班')
    expect(meta.text()).toContain('领班')
  })

  it('只列员工端真有执行点的那三项：库里存着未接线的那七项也不画成能力', async () => {
    // 花名册上可勾的只有三项，但库列里可能存着未接线那七项的键（键保留是契约层的向后
    // 兼容）。它们对应的全是超级管理员在电脑端的活，员工端零执行点 —— 画出来就是"勾了
    // 不生效"那类缺陷的另一面。
    const { wrapper } = await mountToday({
      caps: ['daily_review', 'data', 'roster', 'standard'],
      permission: '管理员',
    })
    const card = wrapper.get('.tA-card.caps')
    for (const label of [
      '仪容仪表',
      '标准图管理',
      '工作区与检查项',
      '花名册与排班',
      '红黑榜与教材',
      '时限设置',
      '数据与归档',
    ]) {
      expect(card.text()).not.toContain(label)
    }
    // 卡上就三行，一项不多。
    expect(wrapper.findAll('.cap-row')).toHaveLength(3)
  })
})
