import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const handlersByTopic = new Map()
const init = vi.fn()
const subscribe = vi.fn()
const unsubscribe = vi.fn()
const on = vi.fn((topic, handler) => {
  if (!handlersByTopic.has(topic)) handlersByTopic.set(topic, new Set())
  handlersByTopic.get(topic).add(handler)
  return () => handlersByTopic.get(topic)?.delete(handler)
})

vi.mock('../../stores/realtime.js', () => ({
  useRealtimeStore: () => ({ init, subscribe, unsubscribe, on }),
}))

let mountHooks = []
let unmountHooks = []

vi.mock('vue', () => ({
  onMounted: (fn) => { mountHooks.push(fn) },
  onBeforeUnmount: (fn) => { unmountHooks.push(fn) },
}))

const { useNudgePull, NUDGE_PULL_COALESCE_MS } = await import('../useNudgePull.js')

function flushMount() {
  for (const fn of mountHooks) fn()
}

function flushUnmount() {
  for (const fn of unmountHooks) fn()
}

function emitTopic(topic, scope = {}) {
  const handlers = handlersByTopic.get(topic)
  if (!handlers) return
  for (const h of handlers) h(scope)
}

beforeEach(() => {
  handlersByTopic.clear()
  init.mockClear()
  subscribe.mockClear()
  unsubscribe.mockClear()
  on.mockClear()
  mountHooks = []
  unmountHooks = []
  vi.useFakeTimers()
})

afterEach(() => {
  flushUnmount()
  vi.useRealTimers()
})

describe('useNudgePull (KDS)', () => {
  it('auto-binds on mount and tears down on unmount', () => {
    const pull = vi.fn()
    useNudgePull({ id: 'kds-t1', topics: ['orders'], pull })
    flushMount()

    expect(init).toHaveBeenCalled()
    expect(subscribe).toHaveBeenCalledWith('kds-t1', ['orders'], {})
    emitTopic('orders')
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).toHaveBeenCalledTimes(1)

    flushUnmount()
    expect(unsubscribe).toHaveBeenCalledWith('kds-t1')
  })

  it('does not pull on bind unless immediate', () => {
    const pull = vi.fn()
    useNudgePull({ id: 'kds-t2', topics: ['orders'], pull, immediate: true })
    flushMount()
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('ignores non-matching topics by default', () => {
    const pull = vi.fn()
    useNudgePull({ id: 'kds-t3', topics: ['orders'], pull })
    flushMount()
    emitTopic('admin')
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).not.toHaveBeenCalled()
  })

  it('honors custom match (scope filter)', () => {
    const pull = vi.fn()
    useNudgePull({
      id: 'kds-t4',
      topics: ['orders'],
      pull,
      match: (ev) => ev.type === 'nudge' && ev.topic === 'orders' && ev.scope?.station === 'changfen',
    })
    flushMount()
    emitTopic('orders', { station: 'xibing' })
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).not.toHaveBeenCalled()
    emitTopic('orders', { station: 'changfen' })
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('coalesces nudges by default (~300ms)', () => {
    const pull = vi.fn()
    useNudgePull({ id: 'kds-t5', topics: ['orders'], pull })
    flushMount()
    emitTopic('orders')
    emitTopic('orders')
    emitTopic('orders')
    expect(pull).not.toHaveBeenCalled()
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('honors explicit debounceMs override', () => {
    const pull = vi.fn()
    useNudgePull({ id: 'kds-t5b', topics: ['orders'], pull, debounceMs: 500 })
    flushMount()
    emitTopic('orders')
    emitTopic('orders')
    expect(pull).not.toHaveBeenCalled()
    vi.advanceTimersByTime(500)
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('manual mode exposes bind/teardown/setFilters', () => {
    const pull = vi.fn()
    const api = useNudgePull({
      id: 'kds-t6',
      topics: ['orders'],
      pull,
      manual: true,
      filters: { date: '2026-07-20' },
    })
    expect(subscribe).not.toHaveBeenCalled()

    api.bind()
    expect(subscribe).toHaveBeenCalledWith('kds-t6', ['orders'], { date: '2026-07-20' })

    api.setFilters({ date: '2026-07-21' })
    expect(subscribe).toHaveBeenLastCalledWith('kds-t6', ['orders'], { date: '2026-07-21' })

    api.teardown()
    expect(unsubscribe).toHaveBeenCalledWith('kds-t6')
    emitTopic('orders')
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pull).not.toHaveBeenCalled()
  })

  it('reconcile pulls immediately and bypasses coalesce', () => {
    const pull = vi.fn()
    useNudgePull({ id: 'kds-t7a', topics: ['orders'], pull })
    flushMount()
    emitTopic('orders') // pending coalesce
    emitTopic('orders', { reconcile: true })
    expect(pull).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    // pending coalesce was cancelled by reconcile
    expect(pull).toHaveBeenCalledTimes(1)
  })

  it('fallback:false ignores reconcile scopes', () => {
    const pullB = vi.fn()
    useNudgePull({ id: 'kds-t7b', topics: ['orders'], pull: pullB, fallback: false })
    flushMount()
    emitTopic('orders', { reconcile: true })
    expect(pullB).not.toHaveBeenCalled()
    emitTopic('orders', {})
    vi.advanceTimersByTime(NUDGE_PULL_COALESCE_MS)
    expect(pullB).toHaveBeenCalledTimes(1)
  })
})
