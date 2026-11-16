import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// realtime.js 一 import 就会用 storage.js 的两个静态管理器拼 URL；这里给最小桩，
// 免得把 uni.getStorageSync / plus 之类平台 API 拖进单测。
vi.mock('../storage.js', () => ({
  ApiAuthManager: { getToken: () => 'kds-token' },
  ApiSettingsManager: { getBaseUrl: () => 'http://kds.local' }
}))

const { AUTH_FAILED_CLOSE_CODE, RealtimeConnection } = await import('../realtime.js')

function makeSocket() {
  const handlers = {}
  return {
    handlers,
    onOpen: (fn) => { handlers.open = fn },
    onMessage: (fn) => { handlers.message = fn },
    onClose: (fn) => { handlers.close = fn },
    onError: (fn) => { handlers.error = fn },
    send: vi.fn(),
    close: vi.fn()
  }
}

let sockets = []

describe('RealtimeConnection 鉴权失败', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sockets = []
    globalThis.uni = {
      connectSocket: vi.fn(() => {
        const socket = makeSocket()
        sockets.push(socket)
        return socket
      })
    }
  })

  afterEach(() => {
    vi.useRealTimers()
    delete globalThis.uni
  })

  it('关闭码 4401 时标记 auth_failed，不再 3s 无限重连', () => {
    const conn = new RealtimeConnection()
    conn.connect()
    expect(sockets).toHaveLength(1)

    sockets[0].handlers.open()
    expect(conn.status).toBe('connected')

    sockets[0].handlers.close({ code: AUTH_FAILED_CLOSE_CODE, reason: 'unauthorized' })
    expect(conn.status).toBe('auth_failed')
    expect(conn.pingTimer).toBeNull()

    // 令牌失效重连多少次都是 4401：推进足够久也不应新建连接。
    vi.advanceTimersByTime(60000)
    expect(globalThis.uni.connectSocket).toHaveBeenCalledTimes(1)
    expect(sockets).toHaveLength(1)
  })

  it('普通断开仍按 3s 重连，重连成功回到 connected', () => {
    const conn = new RealtimeConnection()
    conn.connect()
    sockets[0].handlers.open()

    sockets[0].handlers.close({ code: 1006 })
    expect(conn.status).toBe('reconnecting')

    vi.advanceTimersByTime(3000)
    expect(globalThis.uni.connectSocket).toHaveBeenCalledTimes(2)

    sockets[1].handlers.open()
    expect(conn.status).toBe('connected')
  })

  it('管理员换好令牌后手动 connect() 能从 auth_failed 恢复', () => {
    const conn = new RealtimeConnection()
    conn.connect()
    sockets[0].handlers.open()
    sockets[0].handlers.close({ code: AUTH_FAILED_CLOSE_CODE })

    conn.connect()
    expect(sockets).toHaveLength(2)
    sockets[1].handlers.open()
    expect(conn.status).toBe('connected')
  })
})
