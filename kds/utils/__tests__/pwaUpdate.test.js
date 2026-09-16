import { describe, expect, it, vi } from 'vitest'
import { createPwaUpdateController, isSecurePwaContext } from '../pwaUpdate.js'

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

describe('KDS PWA update controller', () => {
  it('requires a secure or localhost context', () => {
    const serviceWorker = {}
    expect(isSecurePwaContext({ protocol: 'https:', hostname: 'shop.example' }, serviceWorker)).toBe(true)
    expect(isSecurePwaContext({ protocol: 'http:', hostname: 'localhost' }, serviceWorker)).toBe(true)
    expect(isSecurePwaContext({ protocol: 'http:', hostname: '10.0.0.8' }, serviceWorker)).toBe(false)
    expect(isSecurePwaContext({ protocol: 'https:', hostname: 'shop.example' }, null)).toBe(false)
  })

  it('registers once and exposes an already waiting update', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    const registration = new FakeRegistration()
    registration.waiting = { postMessage: vi.fn() }
    serviceWorker.register.mockResolvedValue(registration)
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
    })

    expect(await controller.initialize()).toBe(true)
    expect(await controller.initialize()).toBe(true)
    expect(serviceWorker.register).toHaveBeenCalledTimes(1)
    expect(serviceWorker.register).toHaveBeenCalledWith('/kds/sw.js', {
      scope: '/kds/',
      updateViaCache: 'none',
    })
    expect(controller.snapshot().visible).toBe(true)
  })

  it('honours confirmation before activating a waiting worker', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    const waitingWorker = {
      postMessage: vi.fn(() => serviceWorker.emit('controllerchange')),
    }
    const registration = new FakeRegistration({ waiting: waitingWorker })
    serviceWorker.register.mockResolvedValue(registration)
    const confirmApply = vi.fn(async () => true)
    const reload = vi.fn()
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
      confirmApply,
      reload,
    })

    await controller.initialize()
    expect(await controller.applyUpdate()).toBe(true)
    expect(confirmApply).toHaveBeenCalledTimes(1)
    expect(waitingWorker.postMessage).toHaveBeenCalledWith({ type: 'SKIP_WAITING' })
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('does not reload when the operator cancels the confirmation', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    serviceWorker.controller = {}
    const registration = new FakeRegistration({
      waiting: { postMessage: vi.fn() },
    })
    serviceWorker.register.mockResolvedValue(registration)
    const reload = vi.fn()
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'https:', hostname: 'shop.example' },
      confirmApply: async () => false,
      reload,
    })

    await controller.initialize()
    expect(await controller.applyUpdate()).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  it('degrades without touching the page in an insecure context', async () => {
    const serviceWorker = new FakeServiceWorkerContainer()
    const controller = createPwaUpdateController({
      serviceWorker,
      locationRef: { protocol: 'http:', hostname: '10.0.0.8' },
    })
    expect(await controller.initialize()).toBe(false)
    expect(serviceWorker.register).not.toHaveBeenCalled()
  })
})
