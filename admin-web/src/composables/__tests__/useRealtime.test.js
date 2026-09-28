import { nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useRealtime } from '../useRealtime'

class FakeWebSocket {
  static instances = []

  constructor(url) {
    this.url = url
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

function stubBrowser() {
  vi.stubGlobal('window', { location: { protocol: 'http:', host: 'localhost' } })
  vi.stubGlobal('performance', { now: () => 0 })
  vi.stubGlobal('WebSocket', FakeWebSocket)
}

afterEach(() => {
  FakeWebSocket.instances = []
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useRealtime', () => {
  it('starts only when enabled and closes when disabled', async () => {
    stubBrowser()
    const enabled = ref(true)
    useRealtime(vi.fn(), { enabled })
    await nextTick()
    expect(FakeWebSocket.instances).toHaveLength(1)

    enabled.value = false
    await nextTick()
    expect(FakeWebSocket.instances[0].closeCalled).toBeGreaterThan(0)
  })

  it('不带身份声明时连接 URL 与改动前逐字一致', async () => {
    stubBrowser()
    useRealtime(vi.fn(), { enabled: true })
    await nextTick()
    expect(FakeWebSocket.instances[0].url).toBe('ws://localhost/ws/realtime')
  })

  it('员工端的声明写进连接 URL，管理端不写', async () => {
    stubBrowser()
    const identity = ref('staff')
    useRealtime(vi.fn(), { enabled: true, identity })
    await nextTick()
    expect(FakeWebSocket.instances[0].url).toBe('ws://localhost/ws/realtime?identity=staff')

    // 身份换成管理端（页面换了）：旧连接关掉，新连接不带声明——声明只在建连时生效，
    // 不重连就会顶着员工身份的旧连接继续用。
    identity.value = null
    await nextTick()
    expect(FakeWebSocket.instances[0].closeCalled).toBeGreaterThan(0)
    expect(FakeWebSocket.instances.at(-1).url).toBe('ws://localhost/ws/realtime')
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('身份声明不变时不重连', async () => {
    stubBrowser()
    const identity = ref('staff')
    useRealtime(vi.fn(), { enabled: true, identity })
    await nextTick()
    expect(FakeWebSocket.instances).toHaveLength(1)

    identity.value = 'staff'
    await nextTick()
    expect(FakeWebSocket.instances).toHaveLength(1)
  })
})
