import { describe, expect, it, vi } from 'vitest'
import { createPwaUpdateController, isSecurePwaContext } from '../usePwaUpdate'

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
})
