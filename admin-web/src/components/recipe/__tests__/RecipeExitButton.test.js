// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import RecipeExitButton from '../RecipeExitButton.vue'
import { useImageUploadQueueStore } from '../../../stores/imageUploadQueue'

// 配方阅读面（列表 / 详情 / 打印 / 印码）顶栏的退出入口（票 10，spec 故事 47）：
// 这四个页面是沉浸页、不套工作台外壳，所以没有外壳那颗退出按钮可挂 —— 但动作与
// `components/workbench/WorkbenchExitButton.vue` 是**同一个**（`useWorkbenchLogout`
// 按此刻的身份派发：扫码进来的厨师是员工会话，店长是管理端会话）。
// 这里只断言外部行为：点了之后实际发哪个登出请求、落到哪里。

let fetchMock

async function mountButton({ identity = 'staff', path = '/workbench/kitchen/recipe/detail?slug=changfen' } = {}) {
  vi.resetModules()
  fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
  vi.stubGlobal('fetch', fetchMock)

  const [{ default: Button }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../RecipeExitButton.vue'),
    import('../../../stores/workbenchIdentity'),
  ])

  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkbenchIdentityStore()
  store.identity = identity
  store.sessions = { admin: identity === 'super', staff: identity === 'staff' }
  store.available = identity ? [identity] : []

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', component: { template: '<div />' } },
      { path: '/workbench/kitchen/recipe/detail', component: { template: '<div />' } },
      { path: '/workbench/kitchen/recipe', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(Button, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router }
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

describe('配方阅读面的退出入口', () => {
  it('扫码进来的员工：走员工登出，回登录页并带上那条配方（退出后回得来）', async () => {
    const { wrapper, router } = await mountButton({ identity: 'staff' })

    expect(wrapper.text()).toContain('退出')
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(1)
    expect(callsTo('/api/auth/logout')).toHaveLength(0)
    expect(router.currentRoute.value.path).toBe('/login')
    // 原目标带 query：登录面板据此默认开员工栏，登录后回到这一条配方。
    expect(router.currentRoute.value.query.next).toBe(
      '/workbench/kitchen/recipe/detail?slug=changfen',
    )
  })

  it('店长：走管理端登出（同一个动作的另一档）', async () => {
    const { wrapper, router } = await mountButton({ identity: 'super' })

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(callsTo('/api/auth/logout')).toHaveLength(1)
    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(0)
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe(
      '/workbench/kitchen/recipe/detail?slug=changfen',
    )
  })

  it('员工还有照片没传完：先弹确认，确认之后才真退（员工那侧的队列与确认一个字没丢）', async () => {
    const { wrapper, router } = await mountButton({ identity: 'staff' })
    useImageUploadQueueStore().tasks.push({ id: 'q1', status: 'queued', transport: 'staff' })

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(0)
    expect(router.currentRoute.value.path).toBe('/workbench/kitchen/recipe/detail')

    const confirm = wrapper.findAll('button').find((node) => node.text().includes('仍然退出'))
    expect(confirm).toBeTruthy()
    await confirm.trigger('click')
    await flushPromises()

    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(1)
    expect(router.currentRoute.value.path).toBe('/login')
  })
})
