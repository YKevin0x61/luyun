import { describe, expect, it } from 'vitest'
import { connectionEdge } from '../connectionEdge.js'

describe('connectionEdge', () => {
  it('emits disconnect when leaving connected', () => {
    expect(connectionEdge('connected', 'disconnected')).toBe('disconnect')
    expect(connectionEdge('connected', 'reconnecting')).toBe('disconnect')
  })

  it('ignores other transitions', () => {
    expect(connectionEdge('disconnected', 'connected')).toBe(null)
    expect(connectionEdge('reconnecting', 'disconnected')).toBe(null)
    expect(connectionEdge('connected', 'connected')).toBe(null)
    expect(connectionEdge(undefined, 'disconnected')).toBe(null)
  })
})
