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

async function mountButton(path = '/staff/today') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', component: { template: '<div />' } },
      { path: '/staff/today', component: { template: '<div />' } },
      { path: '/staff/clean', component: { template: '<div />' } },
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
    const { wrapper, router } = await mountButton('/staff/clean')

    await wrapper.get('button').trigger('click')
    await flushPromises()

    const calls = logoutCalls()
    expect(calls).toHaveLength(1)
    expect(calls[0][1].method).toBe('POST')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/staff/clean')
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

  it('三张员工页都挂同一颗共用按钮，卫生首页不再自己实现退出', () => {
    const pages = [
      '../../../views/today/TodayView.vue',
      '../../../views/today/TodayMonthView.vue',
      '../../../views/hygiene/HygieneHomeView.vue',
    ]
    for (const rel of pages) {
      expect(readFileSync(join(here, rel), 'utf8'), rel).toMatch(/<StaffExitButton \/>/)
    }
    const home = readFileSync(join(here, '../../../views/hygiene/HygieneHomeView.vue'), 'utf8')
    expect(home).not.toMatch(/askLogout/)
    expect(home).not.toMatch(/staff\/logout/)
  })
})
