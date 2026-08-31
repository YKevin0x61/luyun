import { describe, expect, it } from 'vitest'
import {
  RECIPE_BRAND_MARK,
  RECIPE_BRAND_TAGLINE,
  RECIPE_BRAND_TITLE,
  RECIPE_DASHBOARD_BLURB,
  RECIPE_NAV_HOME_LABEL,
  RECIPE_NAV_MANAGE_LABEL,
  RECIPE_NAV_STATIONS_LABEL,
  recipeCountLabel,
  recipeDocumentTitle,
} from '../recipeCopy.js'

const CHROME = [
  RECIPE_BRAND_MARK,
  RECIPE_BRAND_TITLE,
  RECIPE_BRAND_TAGLINE,
  RECIPE_NAV_HOME_LABEL,
  RECIPE_NAV_STATIONS_LABEL,
  RECIPE_NAV_MANAGE_LABEL,
  RECIPE_DASHBOARD_BLURB,
].join(' ')

describe('recipe chrome copy', () => {
  it('不出现 SOP、条目、英文指挥舱词', () => {
    expect(CHROME).not.toMatch(/SOP|条目|Command Center|检核|Markdown/i)
  })
})

describe('recipeDocumentTitle', () => {
  it('岗位名跟在配方后面', () => {
    expect(recipeDocumentTitle('肠粉档')).toBe('肠粉档 · 配方')
  })

  it('空标题只留模块名', () => {
    expect(recipeDocumentTitle('')).toBe('配方')
    expect(recipeDocumentTitle(null)).toBe('配方')
  })
})

describe('recipeCountLabel', () => {
  it('用条配方，不用条目', () => {
    expect(recipeCountLabel(27)).toBe('27 条配方')
    expect(recipeCountLabel(0)).toBe('0 条配方')
    expect(recipeCountLabel(undefined)).toBe('0 条配方')
    expect(recipeCountLabel(27)).not.toMatch(/条目/)
  })
})
