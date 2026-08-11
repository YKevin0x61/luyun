import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listeners = new Set()
const subscribe = vi.fn()
const unsubscribe = vi.fn()
const connected = { value: true }
let mountHooks = []
let unmountHooks = []
let watchCallback = null

vi.mock('vue', () => ({
  inject: (key, fallback = null) => {
    if (key === 'onRealtimeEvent') {
      return (fn) => {
        listeners.add(fn)
        return () => listeners.delete(fn)
      }
    }
    if (key === 'wsSubscribe') return subscribe
    if (key === 'wsUnsubscribe') return unsubscribe
    if (key === 'wsConnected') return connected
    return fallback
  },
  onMounted: (fn) => { mountHooks.push(fn) },
  onBeforeUnmount: (fn) => { unmountHooks.push(fn) },
  watch: (source, cb) => {
    watchCallback = cb
    return () => { watchCallback = null }
  },
}))

// useConnectionFallback 依赖 watch/inject；用真实模块 + 上面的 vue mock
const { useNudgePull, NUDGE_PULL_COALESCE_MS } = await import('../useNudgePull.js')

function flushMount() {
  for (const fn of mountHooks) fn()
}

function flushUnmount() {
  for (const fn of unmountHooks) fn()
}

function emitNudge(topic, scope = {}) {
  for (const fn of listeners) fn({ type: 'nudge', topic, scope })
}

beforeEach(() => {
  listeners.clear()
  subscribe.mockClear()
  unsubscribe.mockClear()
  mountHooks = []
  unmountHooks = []
  watchCallback = null
  connected.value = true
  vi.useFakeTimers()
})

afterEach(() => {
  flushUnmount()
  vi.useRealTimers()
})

describe('useNudgePull', () => {
  it('auto-binds on mount and tears down on unmount', () => {
    const pull = vi.fn()
    useNudgePull({ id: 't1', topics: ['dashboard'], pull })
    flushMount()

    expect(subscribe).toHaveBeenCalledWith('t1', ['dashboard'], {})
    emitNudge('dashboard')
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).toHaveBeenCalledTimes(1)

    flushUnmount()
    expect(unsubscribe).toHaveBeenCalledWith('t1')
  })

  it('does not pull on bind unless immediate', () => {
    const pull = vi.fn()
    useNudgePull({ id: 't2', topics: ['orders'], pull, immediate: true })
    flushMount()
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('ignores non-matching topics by default', () => {
    const pull = vi.fn()
    useNudgePull({ id: 't3', topics: ['orders'], pull })
    flushMount()
    emitNudge('admin')
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).not.toHaveBeenCalled()
  })

  it('honors custom match (scope filter)', () => {
    const pull = vi.fn()
    useNudgePull({
      id: 't4',
      topics: ['admin'],
      pull,
      match: (ev) => ev.type === 'nudge' && ev.topic === 'admin' && ev.scope?.kind === 'reconcile',
    })
    flushMount()
    emitNudge('admin', { kind: 'other' })
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).not.toHaveBeenCalled()
    emitNudge('admin', { kind: 'reconcile' })
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('coalesces nudges by default (~300ms)', () => {
    const pull = vi.fn()
    useNudgePull({ id: 't5', topics: ['orders'], pull })
    flushMount()
    emitNudge('orders')
    emitNudge('orders')
    expect(pull).not.toHaveBeenCalled()
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('honors explicit debounceMs override', () => {
    const pull = vi.fn()
    useNudgePull({ id: 't5b', topics: ['orders'], pull, debounceMs: 500 })
    flushMount()
    emitNudge('orders')
    emitNudge('orders')
    expect(pull).not.toHaveBeenCalled()
    vi.advanceTimersByTime(500)
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('manual mode exposes bind/teardown/setFilters', () => {
    const pull = vi.fn()
    const api = useNudgePull({
      id: 't6',
      topics: ['orders'],
      pull,
      manual: true,
      filters: { date: '2026-07-20' },
    })
    expect(subscribe).not.toHaveBeenCalled()

    api.bind()
    expect(subscribe).toHaveBeenCalledWith('t6', ['orders'], { date: '2026-07-20' })

    api.setFilters({ date: '2026-07-21' })
    expect(subscribe).toHaveBeenLastCalledWith('t6', ['orders'], { date: '2026-07-21' })

    api.teardown()
    expect(unsubscribe).toHaveBeenCalledWith('t6')
    emitNudge('orders')
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).not.toHaveBeenCalled()
  })

  it('fallback when() skips pull while predicate is false', () => {
    const pull = vi.fn()
    let allow = false
    useNudgePull({
      id: 't7',
      topics: ['logs'],
      pull,
      fallback: { when: () => allow },
    })
    flushMount()

    // 模拟断线：useConnectionFallback 会 watch wsConnected
    connected.value = false
    watchCallback?.(false)
    vi.advanceTimersByTime(8000) // grace
    vi.advanceTimersByTime(30000) // poll interval
    expect(pull).not.toHaveBeenCalled()

    allow = true
    vi.advanceTimersByTime(30000)
    expect(pull).toHaveBeenCalled()
  })

  it('connection fallback pulls on interval without needing coalesce flush', () => {
    const pull = vi.fn()
    useNudgePull({ id: 't8', topics: ['orders'], pull })
    flushMount()
    connected.value = false
    watchCallback?.(false)
    vi.advanceTimersByTime(8000)
    vi.advanceTimersByTime(30000)
    // fallback uses runPullNow — not schedulePull — so interval fire does not wait coalesceMs
    expect(pull).toHaveBeenCalledTimes(1)
  })
})
