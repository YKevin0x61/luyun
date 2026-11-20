import { describe, expect, it, vi, afterEach } from 'vitest'
import { clampFontPx, defaultFontPx } from '../recipeCore.js'

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

describe('defaultFontPx', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function withWidth(matches) {
    vi.stubGlobal('window', {
      matchMedia: (query) => ({ matches: query === '(max-width: 40rem)' ? matches : false }),
    })
  }

  it('defaults to 14px on both narrow and wide screens', () => {
    // 厨房阅读距离：手机/小平板上 12px 认不动；桌面 27 寸下 12px 同样偏小
    // （2026-11-21 电脑端审查）。所以宽窄统一 14px，A− 仍可调到 12px。
    withWidth(true)
    expect(defaultFontPx()).toBe(14)

    withWidth(false)
    expect(defaultFontPx()).toBe(14)
  })

  it('falls back to 14px when there is no matchMedia (SSR / 老环境)', () => {
    vi.stubGlobal('window', {})
    expect(defaultFontPx()).toBe(14)
  })
})
