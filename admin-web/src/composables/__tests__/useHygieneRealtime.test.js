import { describe, expect, it, vi } from 'vitest'

const pull = vi.fn()
const useNudgePull = vi.fn(() => ({ pull }))

vi.mock('../useNudgePull', () => ({
  useNudgePull: (...args) => useNudgePull(...args),
}))

import { useHygieneRealtime } from '../useHygieneRealtime'

describe('useHygieneRealtime', () => {
  it('subscribes to the hygiene topic for the requested resources', () => {
    useHygieneRealtime({
      id: 'staff-home',
      resources: ['daily', 'fix'],
      pull,
    })

    expect(useNudgePull).toHaveBeenCalledWith(expect.objectContaining({
      id: 'staff-home',
      topics: ['hygiene'],
      pull,
    }))
    const options = useNudgePull.mock.calls[0][0]
    expect(options.match({ type: 'nudge', topic: 'hygiene', scope: { resource: 'daily' } })).toBe(true)
    expect(options.match({ type: 'nudge', topic: 'hygiene', scope: { resource: 'boards' } })).toBe(false)
    expect(options.match({ type: 'nudge', topic: 'orders', scope: { resource: 'daily' } })).toBe(false)
  })
})
