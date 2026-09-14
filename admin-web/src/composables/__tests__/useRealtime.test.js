import { nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useRealtime } from '../useRealtime'

class FakeWebSocket {
  static instances = []

  constructor() {
    this.readyState = 0
    this.closeCalled = 0
    FakeWebSocket.instances.push(this)
  }

  send() {}

  close() {
    this.closeCalled += 1
    this.readyState = 3
  }
}

afterEach(() => {
  FakeWebSocket.instances = []
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useRealtime', () => {
  it('starts only when enabled and closes when disabled', async () => {
    vi.stubGlobal('window', { location: { protocol: 'http:', host: 'localhost' } })
    vi.stubGlobal('performance', { now: () => 0 })
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const enabled = ref(true)
    useRealtime(vi.fn(), { enabled })
    await nextTick()
    expect(FakeWebSocket.instances).toHaveLength(1)

    enabled.value = false
    await nextTick()
    expect(FakeWebSocket.instances[0].closeCalled).toBeGreaterThan(0)
  })
})
