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

  it('narrow screens default to 14px, desktop to 12px', () => {
    // 厨房阅读距离：手机/小平板上 12px 认不动（这个是 RecipeDetailView 的默认字号，
    // 它用内联 setProperty 写 --reader-fs，所以只能在这里给默认值，CSS 媒体查询压不住）。
    withWidth(true)
    expect(defaultFontPx()).toBe(14)

    withWidth(false)
    expect(defaultFontPx()).toBe(12)
  })

  it('falls back to 12 when there is no matchMedia (SSR / 老环境)', () => {
    vi.stubGlobal('window', {})
    expect(defaultFontPx()).toBe(12)
  })
})
