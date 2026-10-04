import { describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import { createPwaUpdateController, isSecurePwaContext, usePwaUpdate } from '../usePwaUpdate'

class FakeEventTarget {
  constructor() {
    this.listeners = new Map()
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set())
    this.listeners.get(type).add(listener)
  }

  emit(type) {
    for (const listener of this.listeners.get(type) || []) listener()
  }
}

class FakeRegistration extends FakeEventTarget {
  constructor({ waiting = null, installing = null } = {}) {
    super()
    this.waiting = waiting
    this.installing = installing
    this.update = vi.fn(async () => {})
  }
}

class FakeServiceWorkerContainer extends FakeEventTarget {
  constructor() {
    super()
    this.controller = null
    this.register = vi.fn()
  }
}

describe('usePwaUpdate', () => {
  it('accepts HTTPS and localhost only', () => {
    const serviceWorker = {}
    expect(isSecurePwaContext({ protocol: 'https:', hostname: 'shop.example' }, serviceWorker)).toBe(true)
    expect(isSecurePwaContext({ protocol: 'http:', hostname: 'localhost' }, serviceWorker)).toBe(true)
    expect(isSecurePwaContext({ protocol: 'http:', hostname: '127.0.0.1' }, serviceWorker)).toBe(true)
    expect(isSecurePwaContext({ protocol: 'http:', hostname: '192.168.1.20' }, serviceWorker)).toBe(false)
    expect(isSecurePwaContext({ protocol: 'https:', hostname: 'shop.example' }, null)).toBe(false)
  })

  it('registers on startup and exposes a waiting update', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    const registration = new FakeRegistration()
    registration.waiting = { postMessage: vi.fn() }
    serviceWorker.register.mockResolvedValue(registration)
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
    })

    expect(controller.visible.value).toBe(false)
    expect(await controller.initialize()).toBe(true)
    expect(controller.visible.value).toBe(true)
    expect(serviceWorker.register).toHaveBeenCalledWith('/sw.js', {
      scope: '/',
      updateViaCache: 'none',
    })
  })

  it('applies the waiting worker and reloads on demand', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    const waitingWorker = {
      postMessage: vi.fn(() => serviceWorker.emit('controllerchange')),
    }
    serviceWorker.register.mockResolvedValue(
      new FakeRegistration({ waiting: waitingWorker }),
    )
    const reload = vi.fn()
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
      reload,
    })

    await controller.initialize()
    expect(await controller.apply()).toBe(true)
    expect(waitingWorker.postMessage).toHaveBeenCalledWith({ type: 'SKIP_WAITING' })
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('surfaces apply failures without hiding the prompt', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    const waitingWorker = {
      postMessage: vi.fn(() => {
        throw new Error('network failed')
      }),
    }
    serviceWorker.register.mockResolvedValue(
      new FakeRegistration({ waiting: waitingWorker }),
    )
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
      reload: vi.fn(),
    })

    await controller.initialize()
    expect(await controller.apply()).toBe(false)
    expect(controller.error.value).toBe('network failed')
    expect(controller.visible.value).toBe(true)
    expect(controller.applying.value).toBe(false)
  })

  it('degrades without a service worker', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'http:', hostname: '192.168.1.20' },
    })
    expect(await controller.initialize()).toBe(false)
    expect(serviceWorker.register).not.toHaveBeenCalled()
  })

  it('票 09：按 App 注册 —— 工作台那份是 /workbench/sw.js + scope /workbench', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    serviceWorker.register.mockResolvedValue(new FakeRegistration())
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
    })

    await controller.initialize({
      serviceWorker: '/workbench/sw.js',
      serviceWorkerScope: '/workbench',
    })

    expect(serviceWorker.register).toHaveBeenCalledWith('/workbench/sw.js', {
      scope: '/workbench',
      updateViaCache: 'none',
    })
  })

  it('同一个 App 重复初始化只注册一次，换 App 才补注册并改盯新的那份', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    const rootRegistration = new FakeRegistration()
    rootRegistration.waiting = { postMessage: vi.fn() }
    const workbenchWaiting = { postMessage: vi.fn(() => serviceWorker.emit('controllerchange')) }
    const workbenchRegistration = new FakeRegistration({ waiting: workbenchWaiting })
    serviceWorker.register.mockImplementation(async (url) =>
      url === '/workbench/sw.js' ? workbenchRegistration : rootRegistration,
    )
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
    })

    // 管理面：根那份已经在等更新 → 提示条亮
    await controller.initialize()
    expect(controller.visible.value).toBe(true)

    // 走进工作台（客户端路由，不刷新）：补注册工作台那份，提示条改盯它
    await controller.initialize({
      serviceWorker: '/workbench/sw.js',
      serviceWorkerScope: '/workbench',
    })
    expect(serviceWorker.register).toHaveBeenCalledTimes(2)
    expect(serviceWorker.register).toHaveBeenLastCalledWith('/workbench/sw.js', {
      scope: '/workbench',
      updateViaCache: 'none',
    })
    // 换 App 时旧的等待状态不跟着走，再按新的那份重算
    expect(controller.visible.value).toBe(true)
    expect(await controller.apply()).toBe(true)
    expect(workbenchWaiting.postMessage).toHaveBeenCalledWith({ type: 'SKIP_WAITING' })

    // 同一个 App 再来一次不发请求
    await controller.initialize({
      serviceWorker: '/workbench/sw.js',
      serviceWorkerScope: '/workbench',
    })
    expect(serviceWorker.register).toHaveBeenCalledTimes(2)
    // 切回管理面：注册根那份、重新盯它
    await controller.initialize()
    expect(serviceWorker.register).toHaveBeenCalledTimes(3)
    expect(serviceWorker.register).toHaveBeenLastCalledWith('/sw.js', {
      scope: '/',
      updateViaCache: 'none',
    })
  })

  it('usePwaUpdate：归属判据换了档就补注册那一档的 worker', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    serviceWorker.register.mockResolvedValue(new FakeRegistration())
    const selection = ref({ serviceWorker: '/sw.js', serviceWorkerScope: '/' })

    usePwaUpdate(selection, {
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
    })
    await nextTick()
    await Promise.resolve()
    expect(serviceWorker.register).toHaveBeenCalledTimes(1)

    selection.value = { serviceWorker: '/workbench/sw.js', serviceWorkerScope: '/workbench' }
    await nextTick()
    await Promise.resolve()
    expect(serviceWorker.register).toHaveBeenCalledTimes(2)
    expect(serviceWorker.register).toHaveBeenLastCalledWith('/workbench/sw.js', {
      scope: '/workbench',
      updateViaCache: 'none',
    })

    // 同一档里换路由（判据返回同一个对象）不再注册
    selection.value = selection.value
    await nextTick()
    await Promise.resolve()
    expect(serviceWorker.register).toHaveBeenCalledTimes(2)
  })
})
