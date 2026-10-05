// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import StaffExitButton from '../StaffExitButton.vue'
import { useImageUploadQueueStore } from '../../../stores/imageUploadQueue'

// 票 10：三张员工页顶栏共用的那颗退出。只断言外部行为——点了之后实际发出什么请求、
// 落到哪里，以及「队列里还有照片时先不请求、先弹确认」。

const here = dirname(fileURLToPath(import.meta.url))

let fetchMock
let pinia

async function mountButton(path = '/workbench/me/today') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', component: { template: '<div />' } },
      { path: '/workbench/me/today', component: { template: '<div />' } },
      { path: '/workbench/me/clean', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(StaffExitButton, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router }
}

function logoutCalls() {
  return fetchMock.mock.calls.filter(([url]) => url === '/api/hygiene/staff/logout')
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('员工端共用的退出按钮', () => {
  it('队列空时直接退：打员工登出接口，并回 /login 带上当前页', async () => {
    const { wrapper, router } = await mountButton('/workbench/me/clean')

    await wrapper.get('button').trigger('click')
    await flushPromises()

    const calls = logoutCalls()
    expect(calls).toHaveLength(1)
    expect(calls[0][1].method).toBe('POST')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/me/clean')
  })

  it('队列里还有照片时先确认，确认之后才真退出', async () => {
    const { wrapper, router } = await mountButton()
    useImageUploadQueueStore().tasks.push({ id: 'q1', status: 'queued', transport: 'staff' })

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(logoutCalls()).toHaveLength(0)
    expect(wrapper.text()).toContain('还有照片没传完')
    expect(wrapper.text()).toContain('1 张照片')

    const confirm = wrapper.findAll('button').find((b) => b.text().includes('仍然退出'))
    await confirm.trigger('click')
    await flushPromises()

    expect(logoutCalls()).toHaveLength(1)
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('员工三页**不再**各挂一颗：外壳顶栏那一颗是唯一的一颗（D5）', () => {
    // 原来三页页内各挂一颗 `<StaffExitButton />`，而这三页从票 03 起就套在
    // `WorkbenchLayout` 里 —— 那一条顶栏自己也有一颗（`.wb-exit`，员工那一档同样走
    // `useStaffLogout`）。390 上两颗相距约 300px、都叫「退出」、功能完全重复。
    // 现在页内那三颗撤掉，外壳那一颗覆盖全部工作台页面（含员工首页那种页内没有顶栏的）。
    const pages = [
      '../../../views/today/TodayView.vue',
      '../../../views/today/TodayMonthView.vue',
      '../../../views/hygiene/HygieneHomeView.vue',
    ]
    for (const rel of pages) {
      const source = readFileSync(join(here, rel), 'utf8')
      expect(source, rel).not.toMatch(/<StaffExitButton/)
      expect(source, rel).not.toMatch(/components\/staff\/StaffExitButton/)
    }

    // 那一颗在外壳里，三页共用（`workbenchShell.test.js` 另有一条按员工身份点它的）。
    const shell = readFileSync(join(here, '../../../views/workbench/WorkbenchLayout.vue'), 'utf8')
    expect(shell).toMatch(/<WorkbenchExitButton/)

    // 员工端卫生首页自己的顶栏也退化成页内标题条了：品牌（红色「台」+「工作台」）搬走
    // （D9 —— 原来 390 下被压到 scrollWidth 62 / clientWidth 57，三个字逐字竖排）。
    const home = readFileSync(join(here, '../../../views/hygiene/HygieneHomeView.vue'), 'utf8')
    expect(home).not.toMatch(/class="hy-brand"/)
    expect(home).not.toMatch(/askLogout/)
    expect(home).not.toMatch(/staff\/logout/)
  })
})
