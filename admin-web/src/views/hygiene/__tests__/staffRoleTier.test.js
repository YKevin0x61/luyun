// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

/**
 * 员工卫生页（`/workbench/me/clean`）在**不同的管理权限开关**下的差别
 * （2026-10-05 审查 F-04 / F-05；同日按用户的裁定从"一个档位"换成"十项开关"）。
 *
 * 同目录其它用例多是"读源码断字符串"的契约式断言。这一条跟 `staffHomeRender` 一样
 * **真挂一遍**：F-04 的病灶正是"判据分叉"——队列文案说「查看」、任务卡按钮说「对照」，
 * 字符串断言看得见两处各自写了什么，但看不见**同一个人在同一行上同时看到什么**。
 *
 * 这里的人只换 `admin_caps`（外加开单那几条要的 `zone_id`）：
 * - 有 `daily_review` + `deep_review` + `fix`（= 迁移 0015 给升级前「管理员」回填的那三项）：
 *   待验收那一行是「对照」+ 能通过/驳回，整改那一组有「开整改单」；
 * - 一项都没有：同一行是「查看」+ 一句"等管理员验收"，没有决定按钮，但**重拍还在**；
 * - **只开一项**（本例 `fix`）：只有那一件事跟着开 —— 开整改单在、日常与专项仍是「查看」，
 *   而且「待我验收」那一档连请求都不发（`daily_review` 才拉）。
 *   这一条正是"档位换成开关"的意义：同一个 `permission` 标签下，这两个人能做的不一样。
 *
 * 另一边压 F-05：有「整改单」那一项的人的「开整改单」不再拿"今天排到班"当前置条件，
 * 跟着**工作区**判 —— 有区就开（表单里只有他自己那个区），没区才拦、且拦的时候说人话。
 */

const ZONE = { id: 3, name: '案板' }

// 迁移 0015 给升级前「管理员」回填的三项：升级当场行为不变。
const LEGACY_ADMIN_CAPS = ['daily_review', 'deep_review', 'fix']

const DAILY_PENDING = {
  item_id: 1,
  item_name: '台面',
  zone_id: 3,
  zone_name: '案板',
  shift: '白班',
  status: '待验收',
  submitter_id: 99,
}

const DEEP_PENDING = {
  item_id: 2,
  item_name: '冰箱里面',
  status: '待验收',
  submitter_id: 99,
}

const REVIEW = {
  submitter_id: 99,
  submitted_at: '2026-10-05T10:00:00+08:00',
  frozen_markup: [],
  markup: [],
  before_watermark: null,
  after_watermark: null,
  watermark: null,
}

function employee(overrides) {
  return {
    id: 7,
    name: '余威威',
    phone: '13800000000',
    job_title: '案板',
    permission: '普通员工',
    admin_caps: [],
    shift: '白班',
    zone_id: 3,
    zone_name: '案板',
    zone_shifts: ['白班', '夜班'],
    ...overrides,
  }
}

function tableFor(person) {
  return {
    '/api/hygiene/staff/me': {
      employee: person,
      daily_clocks: { day_hhmm: '18:00', night_hhmm: '02:00' },
      deep_clock: { hhmm: '20:00' },
    },
    '/api/hygiene/staff/daily-work': { items: [DAILY_PENDING] },
    '/api/hygiene/staff/deep-clean': { items: [DEEP_PENDING], status: '待办' },
    '/api/hygiene/staff/fix': { items: [] },
    // 名单是**全店**的（服务端只把每个区底下的检查项按他的区切片，名单本身没切）：
    // 所以这一页拿到的 zones 一直有好几个，不能拿它当"他能开在哪个区"的判据。
    '/api/hygiene/staff/daily-catalog': {
      zones: [{ id: 1, name: '馅档' }, ZONE, { id: 4, name: '熟笼' }],
    },
    '/api/hygiene/staff/boards': { week_start: '2026-09-28', people: [], zones: [] },
    '/api/hygiene/staff/teaching': { items: [] },
    '/api/hygiene/staff/deep-clean/2/review': REVIEW,
    '/api/hygiene/staff/daily/1/review': REVIEW,
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

/** `hasLiveCamera()` 在 jsdom 里恒为 false（没有 mediaDevices），开单表单会被相机那道
 *  守卫拦住 —— 这一条压的不是相机，把权限判据放出来。 */
function stubLiveCamera() {
  Object.defineProperty(window.navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: async () => ({}) },
  })
}

async function mountHome(person) {
  const TABLE = tableFor(person)
  const requested = []
  const fetchMock = vi.fn(async (url) => {
    // 记的是**带 query 的原地址**：`?review=1`（「待我验收」那一档）拉没拉要看得到。
    requested.push(String(url))
    const path = String(url).split('?')[0]
    if (Object.prototype.hasOwnProperty.call(TABLE, path)) return jsonResponse(TABLE[path])
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const { default: HygieneHomeView } = await import('../HygieneHomeView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/me/clean', component: { template: '<div />' } }],
  })
  await router.push('/workbench/me/clean')
  await router.isReady()

  const wrapper = mount(HygieneHomeView, {
    global: { plugins: [router, pinia], stubs: { StandardPhotoCachePanel: true } },
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, requested }
}

function buttonWithText(wrapper, text) {
  return wrapper.findAll('button').find((node) => node.text() === text) || null
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('员工卫生页 · 判据按「管理权限开关」，不按 permission 标签', () => {
  it('一项都没开：待验收那一行是「查看」，点开只读、有说明、没有决定按钮，但重拍还在', async () => {
    const { wrapper } = await mountHome(employee({ permission: '普通员工' }))
    await flushPromises()

    // 队列（首条待办那颗大按钮）与专项组那颗都写「查看」——两处同一个判据。
    expect(wrapper.get('.hy-next-action').text()).toBe('查看')
    const deepActions = wrapper.get('.hy-task-actions')
    expect(deepActions.text()).toContain('查看')
    expect(deepActions.text()).not.toContain('对照')
    // ADR 0071：交这张的人（没有验收权也一样）能重拍替换自己那份待验收 —— 它跟谁能验收无关。
    expect(deepActions.text()).toContain('重拍')

    await buttonWithText(wrapper, '查看').trigger('click')
    await flushPromises()

    const sheet = wrapper.get('.staff-preview')
    expect(sheet.text()).toContain('等管理员验收')
    // 决定区一个都不该有（通过 / 驳回是验收人的事），说明那一句才是空壳感的解药。
    expect(wrapper.find('.staff-decide').exists()).toBe(false)
    expect(buttonWithText(wrapper, '通过')).toBeNull()
    expect(buttonWithText(wrapper, '驳回')).toBeNull()
  })

  it('三项都开着（升级前「管理员」那三项）：同一行是「对照」，点开有通过 / 驳回', async () => {
    const { wrapper } = await mountHome(employee({
      id: 5,
      permission: '管理员',
      admin_caps: LEGACY_ADMIN_CAPS,
    }))
    await flushPromises()

    expect(wrapper.get('.hy-next-action').text()).toBe('验收')
    const deepActions = wrapper.get('.hy-task-actions')
    expect(deepActions.text()).toContain('对照')
    expect(deepActions.text()).not.toContain('查看')

    await buttonWithText(wrapper, '对照').trigger('click')
    await flushPromises()

    expect(wrapper.get('.staff-preview').text()).not.toContain('等管理员验收')
    expect(wrapper.find('.staff-decide').exists()).toBe(true)
    expect(buttonWithText(wrapper, '通过')).not.toBeNull()
    expect(buttonWithText(wrapper, '驳回')).not.toBeNull()
  })

  it('只开「整改单」：日常与专项仍是「查看」，只有开单跟着开', async () => {
    // 这一条是"档位 → 开关"的核心理由：同一个 permission 标签下，只开一项的人不能
    // 因为标签写着「管理员」就拿到另外两项（旧代码正是拿标签一次放行三件事）。
    const { wrapper, requested } = await mountHome(employee({
      id: 5,
      permission: '管理员',
      admin_caps: ['fix'],
    }))
    await flushPromises()

    // 日常那一行按 daily_review 判：没有就是「查看」，不是「验收」。
    expect(wrapper.get('.hy-next-action').text()).toBe('查看')
    expect(wrapper.get('.hy-task-actions').text()).toContain('查看')
    expect(wrapper.get('.hy-task-actions').text()).not.toContain('对照')

    // 「待我验收」那一档只给有 daily_review 的人：没这一项就连请求都不发、也不渲染。
    expect(requested.some((url) => url.includes('review=1'))).toBe(false)
    expect(wrapper.text()).not.toContain('待我验收')

    // 整改单那一项开着：开单按钮在（F-05 起只看工作区，不看班次）。
    expect(buttonWithText(wrapper, '开整改单')).not.toBeNull()
  })

  it('只开「日常验收」：日常能判、待我验收拉得到，专项与整改不跟着开', async () => {
    const { wrapper, requested } = await mountHome(employee({
      id: 5,
      permission: '管理员',
      admin_caps: ['daily_review'],
    }))
    await flushPromises()

    expect(wrapper.get('.hy-next-action').text()).toBe('验收')
    // 专项那一行按 deep_review 判：没开就是「查看」。
    expect(wrapper.get('.hy-task-actions').text()).toContain('查看')
    // 待我验收拉了、也渲染了（有 daily_review）。
    expect(requested.some((url) => url.includes('review=1'))).toBe(true)
    expect(wrapper.text()).toContain('待我验收')
    // 开整改单是 fix 那一项：没开就没有。
    expect(buttonWithText(wrapper, '开整改单')).toBeNull()
  })

  it('一项都没开：看不到「开整改单」（开单要「整改单」那一项，后端 403 不能靠前端放宽）', async () => {
    const { wrapper } = await mountHome(employee({ permission: '普通员工' }))
    expect(buttonWithText(wrapper, '开整改单')).toBeNull()
  })

  it('整改单·今天没排到工作区：说清缺什么，并且不打开表单', async () => {
    const { wrapper } = await mountHome(employee({
      id: 5,
      permission: '管理员',
      admin_caps: ['fix'],
      shift: null,
      zone_id: null,
      zone_name: null,
    }))
    await flushPromises()

    const open = buttonWithText(wrapper, '开整改单')
    expect(open).not.toBeNull()
    await open.trigger('click')
    await flushPromises()

    const alert = wrapper.get('[role="alert"]').text()
    // 说清缺的是什么、去哪儿补，而不是把他打发去找一个自己就属于的角色。
    expect(alert).toContain('今天没有排到你的工作区')
    expect(alert).toContain('开单要在自己的区里开')
    expect(alert).toContain('找超级管理员在排班页')
    // 旧文案（"先找超级管理员确认今天的排班"）不许再回来 —— F-05 报告的原文。
    expect(alert).not.toContain('先找超级管理员确认今天的排班')
    expect(alert).not.toContain('今天没有排到你的班')
    // 没有区就没得开：表单不该打开。
    expect(wrapper.find('#fix-zone').exists()).toBe(false)
  })

  it('整改单·今天有工作区：表单打得开，且只能开在自己那个区上', async () => {
    stubLiveCamera()
    const { wrapper } = await mountHome(employee({
      id: 5,
      permission: '管理员',
      admin_caps: ['fix'],
    }))
    await flushPromises()

    await buttonWithText(wrapper, '开整改单').trigger('click')
    await flushPromises()

    const select = wrapper.get('#fix-zone')
    const options = wrapper.findAll('#fix-zone option')
    // 名单里有三个区，但开单只能开在自己那个：默认选中的必须是他的区，不是名单第一个。
    expect(options.length).toBe(1)
    expect(options[0].text()).toBe('案板')
    expect(select.element.value).toBe('3')
  })

  it('整改单·有区但这区没开他那一档（班次对不上）：仍然能开在自己的区上', async () => {
    stubLiveCamera()
    const { wrapper } = await mountHome(employee({
      id: 5,
      permission: '管理员',
      admin_caps: ['fix'],
      // 排班给的班次这个区没开：日常交不了（页内那条警示条照旧），但开单是复核动作，
      // 只看区、不看班次 —— 拦它就会退回 F-05 那个"必然失败"。
      zone_shifts: ['夜班'],
    }))
    await flushPromises()

    await buttonWithText(wrapper, '开整改单').trigger('click')
    await flushPromises()

    expect(wrapper.get('#fix-zone').element.value).toBe('3')
  })
})
