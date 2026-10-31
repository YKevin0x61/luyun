// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
// 版式那一条按**源码**断：jsdom 不做布局，网格列宽在这里算不出来（先例：
// `views/workbench/__tests__/shellMobileChrome.test.js` 的 `expectMobileRule`）。
const TODAY = readFileSync(join(here, '..', 'TodayView.vue'), 'utf8')

/**
 * 「你能做的事」能力卡（票 01 / ADR 0093）。
 *
 * 病灶：被放权的员工在系统里看不见自己是谁 —— 能做什么、**还缺哪一项**都得靠撞墙试。
 * 这一块卡把十三项开关里**员工端真有执行点的六项**逐条摊开（开了的给说明，没开的写
 * 「超级管理员还没开给你」），并说清这些事都在手机端「卫生」页里做。
 *
 * 这一条跟同目录 `todayAccount.test.js` 一样**真挂一遍**页，断言只压外部行为：
 * 渲染出了什么文案、哪几行、整块在不在。不碰内部结构，也不断言那七项未接线的键。
 * 先例：`hygiene/__tests__/rosterAdminCaps.test.js`（可勾的六项 + 只读的七项）、
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
      { path: '/workbench/staff/today', component: { template: '<div />' } },
      { path: '/workbench/staff/clean', component: { template: '<div />' } },
      { path: '/workbench/staff/overtime', component: { template: '<div />' } },
    ],
  })
  await router.push('/workbench/staff/today')
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

/** t71：点开「完整资料」二级视图，并读出它的内容（折叠后的 5 项应当在这里可达）。 */
async function openMore(wrapper) {
  const entry = wrapper.findAll('button').find((b) => b.text().includes('完整资料'))
  expect(entry, '主表里没有「完整资料」入口').toBeTruthy()
  await entry.trigger('click')
  await flushPromises()
}
function moreText(wrapper) {
  const boxes = wrapper.findAll('.me-meta')
  return boxes[boxes.length - 1].text()
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

  it('没开通的能力也列出来，写明「超级管理员还没开给你」—— 员工由此知道该找谁开（ADR 0103）', async () => {
    // 只开了日常验收：他要能看出「专项验收」不是系统不给，是超级管理员还没开（ADR 0103）。
    const { wrapper } = await mountToday({ caps: ['daily_review'], permission: '管理员' })
    const card = wrapper.get('.tA-card.caps')

    expect(rowFor(wrapper, '专项验收').text()).toContain('超级管理员还没开给你')
    expect(rowFor(wrapper, '整改单').text()).toContain('超级管理员还没开给你')
    // 没开的那两项**不能**顺带把说明也摆出来（摆出来就等于说这项能用）。
    expect(rowFor(wrapper, '专项验收').text()).not.toContain('判专项卫生的前后对照')
    expect(rowFor(wrapper, '整改单').text()).not.toContain('开整改单、验收或驳回整改单')
    // 开了的那一项反过来：给的是说明，不是「还没开给你」。
    expect(rowFor(wrapper, '日常验收').text()).toContain('判别人交的日常检查，看原图与标准图对照')
    expect(rowFor(wrapper, '日常验收').text()).not.toContain('超级管理员还没开给你')
    // 六项都在卡上（不是只列开了的那一项）。
    expect(card.text()).toContain('日常验收')
    expect(card.text()).toContain('专项验收')
    expect(card.text()).toContain('整改单')
  })

  it('完全没有权限的员工看不到这一块，页面其余部分照常', async () => {
    const { wrapper } = await mountToday({ caps: [], permission: '普通员工' })

    // 整块不在：连标题都不该留一个空壳。
    expect(wrapper.find('.tA-card.caps').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('你能做的事')
    expect(wrapper.text()).not.toContain('超级管理员还没开给你')

    // 其余几块一块都不少：页头（我的）、我的成绩、我的资料。
    // （IA 第二层 2026-10-19：「我的班」块从本页删掉，班次/请假/换班归工作台首页；
    // 今天的活那一块 2026-10-14 搬去工作台首页，由 staffHygieneCard.test 盯着。）
    expect(wrapper.get('.tDay').text()).toBe('我的')
    expect(wrapper.find('.tA-card.sched').exists()).toBe(false)
    expect(wrapper.find('.tA-card.hyg').exists()).toBe(false)
    expect(wrapper.find('.tA-card.score').exists()).toBe(true)
    // t71：姓名已折进「完整资料」二级视图 —— 主表不再有它，但点开必须可达
    expect(wrapper.get('.me-meta').text()).not.toContain('余威威')
    await openMore(wrapper)
    expect(moreText(wrapper)).toContain('余威威')
  })

  it('档位标签写着「管理员」但一项开关都没给：照样不渲染（判据是开关，不是标签）', async () => {
    // 库列与开关不一致是合法状态（ADR 0093）。界面给一个「管理员」的头衔、底下什么都做
    // 不了，正是这一票要治的毛病 —— 所以判据只看 `admin_caps`。
    const { wrapper } = await mountToday({ caps: [], permission: '管理员' })
    expect(wrapper.find('.tA-card.caps').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('你能做的事')
  })

  it('能力卡计 6 项，逐项各有落点，加班那项去「加班与补钟」页（§12 第 1 条）', async () => {
    const { wrapper, router } = await mountToday({
      caps: ['daily_review', 'deep_review', 'fix', 'overtime', 'schedule', 'wastage'],
      permission: '管理员',
    })
    const card = wrapper.get('.tA-card.caps')

    // 6 项都摊开（开的给说明，没开的写「超级管理员还没开给你」）
    expect(card.findAll('.cap-row')).toHaveLength(6)

    // 旧脚注那句已删：它说的是「这三项」，在三项时成立、多一项就成了假话
    expect(card.text()).not.toContain('这三项都在手机端')

    // 每一项各有自己的入口，落点写在按钮上（不是页尾一句概括）
    const gos = card.findAll('.cap-go')
    expect(gos).toHaveLength(6)
    const tos = gos.map((btn) => btn.attributes('data-to'))
    expect(new Set(tos).size).toBeGreaterThan(1)
    expect(tos.filter((to) => to === '/workbench/staff/overtime')).toHaveLength(1)

    // 点加班那一项的路，真的落到加班页（不是只挂了个属性）
    const overtime = gos.find((btn) => btn.attributes('data-to') === '/workbench/staff/overtime')
    await overtime.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/workbench/staff/overtime')
  })

  it('档位标签从账号信息里拿掉（信息由这张卡承载）；主表只列 6 行，姓名 / 职位折进二级视图仍可读', async () => {
    const { wrapper } = await mountToday({ caps: ['daily_review'], permission: '管理员' })
    const card = wrapper.get('.tA-card.caps')
    const meta = wrapper.get('.me-meta')

    // 那张卡上带着档位（同一件事只说一遍的地方）。
    // 2026-10-14 起措辞从「现场复核」改成「管理权限」：卡上现在有人事管理与损耗分析
    // 两项不是复核（花名册可勾的共六项）。
    expect(card.text()).toContain('管理权限')
    // 账号信息回到纯资料：档位那一行没了。
    expect(meta.text()).not.toContain('卫生权限')
    expect(meta.text()).not.toContain('管理员')
    // 主表留下的 6 行（t71 折叠后）
    expect(meta.text()).toContain('13800000000')
    expect(meta.text()).toContain('案板')
    expect(meta.text()).toContain('白班')
    // 折走的姓名 / 职位：主表不再有，但点开「完整资料」必须仍可读（不许丢）
    expect(meta.text()).not.toContain('余威威')
    expect(meta.text()).not.toContain('领班')
    await openMore(wrapper)
    expect(moreText(wrapper)).toContain('余威威')
    expect(moreText(wrapper)).toContain('领班')
  })

  it('只列员工端真有执行点的那六项：库里存着未接线的那七项也不画成能力', async () => {
    // 花名册上可勾的只有六项（2026-10-07 加了「加班与补钟审批」，2026-10-14 又加了
    // 「人事管理」与「损耗分析」），
    // 但库列里可能存着未接线那七项的键（键保留是契约层的向后兼容）。它们对应的全是超级
    // 管理员在电脑端的活，员工端零执行点 —— 画出来就是"勾了不生效"那类缺陷的另一面。
    const { wrapper } = await mountToday({
      caps: ['daily_review', 'data', 'roster', 'standard'],
      permission: '管理员',
    })
    const card = wrapper.get('.tA-card.caps')
    for (const label of [
      '仪容仪表',
      '标准图管理',
      '工作区与检查项',
      '花名册',
      '红黑榜与教材',
      '时限设置',
      '数据与归档',
    ]) {
      expect(card.text()).not.toContain(label)
    }
    // 卡上就六行，一项不多。
    expect(wrapper.findAll('.cap-row')).toHaveLength(6)
  })
})

describe('版式：行内入口不能被压成一条竖条', () => {
  it('每一行是**四列** grid，入口那一列显式给 auto', () => {
    // 2026-10-12 实测：`.cap-row` 只写了三列（16px 58px 1fr），第四个子元素「去 ›」
    // 落进**隐式列**、宽度 auto —— 390px 下 1fr 先把剩余宽度吃完，它只剩 26px，
    // `padding: 0 12px` 吃掉 24px，文字换行成两行，三颗按钮变成三根 26×52 的竖条。
    // 这一条钉住「第 4 列必须显式存在」与「按钮不换行」。
    expect(TODAY).toMatch(
      /\.cap-row\s*\{[^}]*grid-template-columns:\s*16px\s+58px\s+1fr\s+auto/,
    )
    expect(TODAY).toMatch(/\.cap-go\s*\{[^}]*white-space:\s*nowrap/)
    // grid item 上 `flex` 是死属性：原来那行 `flex: 0 0 auto` 从没生效，别再写回去。
    expect(TODAY).not.toMatch(/\.cap-go\s*\{[^}]*flex:\s*0\s+0\s+auto/)
  })
})
