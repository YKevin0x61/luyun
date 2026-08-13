import { describe, expect, it, vi } from 'vitest'
import { canWarmupPrinter, createConnectGate } from '../printerConnectGate.js'

describe('canWarmupPrinter', () => {
  it('allows warmup only when platform, switch, and saved device are all present', () => {
    expect(canWarmupPrinter({
      platformSupported: true,
      printEnabled: true,
      deviceAddress: 'AA:BB'
    })).toBe(true)
  })

  it('skips when print is off or no device is saved', () => {
    expect(canWarmupPrinter({
      platformSupported: true,
      printEnabled: false,
      deviceAddress: 'AA:BB'
    })).toBe(false)
    expect(canWarmupPrinter({
      platformSupported: true,
      printEnabled: true,
      deviceAddress: ''
    })).toBe(false)
    expect(canWarmupPrinter({
      platformSupported: false,
      printEnabled: true,
      deviceAddress: 'AA:BB'
    })).toBe(false)
  })
})

describe('createConnectGate', () => {
  it('coalesces overlapping connect calls onto one attempt', async () => {
    const gate = createConnectGate()
    let started = 0
    let release
    const connect = vi.fn(() => {
      started += 1
      return new Promise((resolve) => {
        release = resolve
      })
    })

    const first = gate.run(connect)
    const second = gate.run(connect)
    expect(started).toBe(1)

    release(true)
    await expect(Promise.all([first, second])).resolves.toEqual([true, true])
    expect(connect).toHaveBeenCalledTimes(1)
  })

  it('allows a later connect after the in-flight one finishes', async () => {
    const gate = createConnectGate()
    const connect = vi.fn().mockResolvedValue(true)

    await gate.run(connect)
    await gate.run(connect)
    expect(connect).toHaveBeenCalledTimes(2)
  })
})
