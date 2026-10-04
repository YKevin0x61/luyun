// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import WorkbenchExitButton from '../WorkbenchExitButton.vue'
import { useImageUploadQueueStore } from '../../../stores/imageUploadQueue'

// 工作台自己的退出入口（票 06）：**独立外壳页一个退出按钮都没有**是这一票要补的洞
// （spec 故事 37 / 47）。只断言外部行为 —— 点了之后实际发哪个登出请求、落到哪里，
// 以及员工那侧「队列里还有照片时先问一句」的原样。
//
// 行为只有一处：组件本身不写分支，身份 → 出口的映射在 `composables/useWorkbenchLogout.js`
// （票 10 会把四处登出收敛成一条，届时改那一个文件）。

let fetchMock
let pinia

async function mountButton({ identity = 'super', path = '/workbench' } = {}) {
  vi.resetModules()
  fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
  vi.stubGlobal('fetch', fetchMock)

  const [{ default: Button }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../WorkbenchExitButton.vue'),
    import('../../../stores/workbenchIdentity'),
  ])

  pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkbenchIdentityStore()
  store.identity = identity
  store.sessions = { admin: identity === 'super', staff: identity === 'staff' }
  store.available = identity ? [identity] : []

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', component: { template: '<div />' } },
      { path: '/workbench', component: { template: '<div />' } },
      { path: '/workbench/hr/calendar', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(Button, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router, store }
}

function callsTo(path) {
  return fetchMock.mock.calls.filter(([url]) => url === path)
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('工作台外壳上的退出入口', () => {
  it('店长身份：客户端退管理端会话，落登录页并带上原页（不整页重载）', async () => {
    const { wrapper, router } = await mountButton({ identity: 'super', path: '/workbench' })

    expect(wrapper.text()).toContain('退出')
    await wrapper.get('button').trigger('click')
    await flushPromises()

    const calls = callsTo('/api/auth/logout')
    expect(calls).toHaveLength(1)
    expect(calls[0][1].method).toBe('POST')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench')
    // 员工那条登出请求一个都不发（两套会话各走各的门）。
    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(0)
  })

  it('员工身份：走员工登出，落登录页并带上原页', async () => {
    const { wrapper, router } = await mountButton({ identity: 'staff', path: '/workbench' })

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(1)
    expect(callsTo('/api/auth/logout')).toHaveLength(0)
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench')
  })

  it('员工身份 + 还有照片没传完：先弹确认，确认之后才真退', async () => {
    const { wrapper, router } = await mountButton({ identity: 'staff' })
    useImageUploadQueueStore().tasks.push({ id: 'q1', status: 'queued', transport: 'staff' })

    await wrapper.get('button').trigger('click')
    await flushPromises()

    // 还没发请求、也还没走。
    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(0)
    expect(router.currentRoute.value.path).toBe('/workbench')

    // 确认框是同一个组件（员工端那颗用的也是它），文案与行为都照旧。
    const confirm = wrapper.findAll('button').find((node) => node.text().includes('仍然退出'))
    expect(confirm).toBeTruthy()
    await confirm.trigger('click')
    await flushPromises()

    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(1)
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('身份还没探出来时什么也不做（不替人猜该退哪一套会话）', async () => {
    const { wrapper, router } = await mountButton({ identity: null })

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(fetchMock).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/workbench')
  })

  it('登出请求失败也照样离开（会话可能已经没了），不把人卡在页上', async () => {
    const { wrapper, router } = await mountButton({ identity: 'super' })
    fetchMock.mockRejectedValueOnce(new Error('网络连不上'))

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/login')
  })
})
