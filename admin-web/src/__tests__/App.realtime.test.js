// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import App from '../App.vue'

// 票 08：员工端页面建立的实时连接要显式声明自己是员工（`?identity=staff`），
// 管理端页面不带声明。只断言「真实挂载的 App 实际建出了哪条连接 URL」。

class FakeWebSocket {
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = 0
    FakeWebSocket.instances.push(this)
  }

  send() {}

  close() {
    this.readyState = 3
  }
}

// 与真实 router 同一套 meta 口径（`/staff/*` 是 public + staffAuth + realtime）。
const ROUTES = [
  { path: '/', component: { template: '<div />' } },
  { path: '/hygiene/roster', component: { template: '<div />' }, meta: { standalone: true } },
  { path: '/login', component: { template: '<div />' }, meta: { standalone: true, public: true } },
  {
    path: '/staff/today',
    component: { template: '<div />' },
    meta: { public: true, standalone: true, staffPhone: true, staffAuth: true, realtime: true },
  },
]

function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountApp(path) {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(path)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(App, {
    global: {
      plugins: [router, pinia],
      stubs: { NavBar: true, PwaUpdateBanner: true, ImageUploadQueuePanel: true },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

const baseUrl = () => `ws://${window.location.host}/ws/realtime`

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  // App 挂载后档口 store 会拉 /api/stations；这里不让它真发请求。
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse([])))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('App 实时连接的身份声明', () => {
  it('员工端页面建立的连接带 `?identity=staff`', async () => {
    const { wrapper } = await mountApp('/staff/today')

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toBe(`${baseUrl()}?identity=staff`)
    wrapper.unmount()
  })

  it('管理端页面不带声明（默认优先级本来就对）', async () => {
    const { wrapper } = await mountApp('/')
    expect(FakeWebSocket.instances[0].url).toBe(baseUrl())

    wrapper.unmount()
  })

  it('公开的独立页（登录页）不建实时连接', async () => {
    const { wrapper } = await mountApp('/login')

    expect(FakeWebSocket.instances).toHaveLength(0)
    wrapper.unmount()
  })

  it('SPA 里从管理端页切到员工页：连接换成带声明的那条', async () => {
    const { wrapper, router } = await mountApp('/')
    expect(FakeWebSocket.instances[0].url).toBe(baseUrl())

    await router.push('/staff/today')
    await flushPromises()

    expect(FakeWebSocket.instances.at(-1).url).toBe(`${baseUrl()}?identity=staff`)
    wrapper.unmount()
  })
})
