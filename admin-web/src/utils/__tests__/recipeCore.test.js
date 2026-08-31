import { describe, expect, it } from 'vitest'
import { clampFontPx } from '../recipeCore.js'

describe('clampFontPx', () => {
  it('clamps to 12–20 and defaults invalid values to 12', () => {
    expect(clampFontPx(12)).toBe(12)
    expect(clampFontPx(14)).toBe(14)
    expect(clampFontPx(9)).toBe(12)
    expect(clampFontPx(99)).toBe(20)
    expect(clampFontPx('16')).toBe(16)
    expect(clampFontPx('x')).toBe(12)
  })
})
