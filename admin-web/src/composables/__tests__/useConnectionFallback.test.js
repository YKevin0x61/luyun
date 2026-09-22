import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// useConnectionFallback 只依赖 vue 的 inject/watch/onBeforeUnmount，测试环境是
// node（无 DOM）。沿用 useNudgePull.test.js 的写法：手动驱动 wsConnected 的
// watcher，观察 refreshFn 的调用次数与时机。
const connected = { value: true }
let unmountHooks = []
let watchCallback = null

vi.mock('vue', () => ({
  inject: (key, fallback = null) => (key === 'wsConnected' ? connected : fallback),
  onBeforeUnmount: (fn) => { unmountHooks.push(fn) },
  watch: (source, cb) => {
    watchCallback = cb
    return () => { watchCallback = null }
  },
}))

const { useConnectionFallback, FALLBACK_GRACE_MS, FALLBACK_POLL_MS } =
  await import('../useConnectionFallback.js')

/** 模拟 WS 连接状态翻转（真实环境由 App.vue 的 connected ref 触发 watcher）。 */
function setConnected(next) {
  connected.value = next
  watchCallback?.(next)
}

beforeEach(() => {
  connected.value = true
  unmountHooks = []
  watchCallback = null
  vi.useFakeTimers()
})

afterEach(() => {
  for (const fn of unmountHooks) fn()
  vi.useRealTimers()
})

describe('useConnectionFallback', () => {
  it('does not pull on first bind while already connected', () => {
    const refresh = vi.fn()
    useConnectionFallback(refresh)
    expect(refresh).not.toHaveBeenCalled()
  })

  it('pulls exactly once when a short disconnect recovers inside the grace window', () => {
    const refresh = vi.fn()
    useConnectionFallback(refresh)

    // 断连 7s（< FALLBACK_GRACE_MS）：兜底轮询还没起，期间的服务端 nudge 收不到
    setConnected(false)
    vi.advanceTimersByTime(FALLBACK_GRACE_MS - 1000)
    expect(refresh).not.toHaveBeenCalled()

    // 恢复：必须补一次拉取，否则这段窗口内的变更永远对不上
    setConnected(true)
    expect(refresh).toHaveBeenCalledTimes(1)

    // 补拉之后仍回到 nudge 驱动，不留周期性请求
    vi.advanceTimersByTime(FALLBACK_POLL_MS * 3)
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('keeps polling on a long disconnect and pulls once more on recovery', () => {
    const refresh = vi.fn()
    useConnectionFallback(refresh)

    setConnected(false)
    vi.advanceTimersByTime(FALLBACK_GRACE_MS)
    vi.advanceTimersByTime(FALLBACK_POLL_MS)
    expect(refresh).toHaveBeenCalledTimes(1)

    setConnected(true)
    expect(refresh).toHaveBeenCalledTimes(2)

    vi.advanceTimersByTime(FALLBACK_POLL_MS * 3)
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('pulls once per reconnect, not more', () => {
    const refresh = vi.fn()
    useConnectionFallback(refresh)

    setConnected(false)
    setConnected(true)
    expect(refresh).toHaveBeenCalledTimes(1)

    setConnected(false)
    setConnected(true)
    expect(refresh).toHaveBeenCalledTimes(2)
  })
})
