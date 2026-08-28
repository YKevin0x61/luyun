import { describe, expect, it } from 'vitest'
import { scaleAmount, servingsFactor } from '../recipeServings.js'

describe('servingsFactor', () => {
  it('two cards with different bases under one target get different factors', () => {
    const target = 8
    expect(servingsFactor(target, 4)).toBe(2)
    expect(servingsFactor(target, 2)).toBe(4)
    expect(servingsFactor(target, 4)).not.toBe(servingsFactor(target, 2))
  })

  it('missing or non-positive base returns 1, never Infinity or NaN', () => {
    expect(servingsFactor(8, null)).toBe(1)
    expect(servingsFactor(8, undefined)).toBe(1)
    expect(servingsFactor(8, 0)).toBe(1)
    expect(servingsFactor(8, -4)).toBe(1)
    expect(servingsFactor(8, 'x')).toBe(1)
    expect(Number.isFinite(servingsFactor(8, 0))).toBe(true)
  })

  it('clamps through clampFactor', () => {
    expect(servingsFactor(0, 4)).toBe(0.1)
    expect(servingsFactor(400, 1)).toBe(99)
  })
})

describe('scaleAmount', () => {
  it('scales a bare numeric amount without a unit suffix', () => {
    expect(scaleAmount('200', 2)).toBe('400')
    expect(scaleAmount('0.5', 2)).toBe('1')
  })

  it('scales range and fraction amounts without a unit', () => {
    expect(scaleAmount('2-3', 2)).toBe('4-6')
    expect(scaleAmount('1/2', 2)).toBe('1')
  })

  it('keeps optional unit suffix when present', () => {
    expect(scaleAmount('80g', 2)).toBe('160g')
    expect(scaleAmount('3-4只', 2)).toBe('6-8只')
    expect(scaleAmount('1/2斤', 2)).toBe('1斤')
  })

  it('leaves non-numeric amount unchanged and never emits NaN', () => {
    expect(scaleAmount('适量', 3)).toBe('适量')
    expect(scaleAmount('按口味调整', 2)).toBe('按口味调整')
    expect(scaleAmount('', 2)).toBe('')
    expect(scaleAmount(null, 2)).toBe('')
    expect(scaleAmount('适量', 3).includes('NaN')).toBe(false)
    expect(scaleAmount('200', Number.NaN)).not.toMatch(/NaN/)
  })

  it('reuses formatQty rounding', () => {
    expect(scaleAmount('1.333', 2)).toBe('2.67')
  })
})
