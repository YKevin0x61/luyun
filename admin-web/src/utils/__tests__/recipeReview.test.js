import { describe, expect, it } from 'vitest'
import { filterRecipesByReview, recipeHasLegacyMarkdown } from '../recipeReview.js'

const SAMPLE = [
  { id: 1, recipe_name: '待复核', needs_review: 1 },
  { id: 2, recipe_name: '已确认', needs_review: 0 },
  { id: 3, recipe_name: '也待复核', needs_review: 1 },
  { id: 4, recipe_name: '缺字段' },
]

describe('filterRecipesByReview', () => {
  it('reviewOnly 关闭时保持原顺序返回全部条目', () => {
    expect(filterRecipesByReview(SAMPLE, false)).toEqual(SAMPLE)
  })

  it('reviewOnly 打开时只保留 needs_review 为 1 的条目', () => {
    expect(filterRecipesByReview(SAMPLE, true)).toEqual([
      { id: 1, recipe_name: '待复核', needs_review: 1 },
      { id: 3, recipe_name: '也待复核', needs_review: 1 },
    ])
  })

  it('把 truthy 的 1 / true / "1" 都当成待复核', () => {
    expect(filterRecipesByReview([
      { id: 1, needs_review: true },
      { id: 2, needs_review: '1' },
      { id: 3, needs_review: 1 },
      { id: 4, needs_review: '0' },
    ], true).map((row) => row.id)).toEqual([1, 2, 3])
  })

  it('空列表与缺省列表返回空数组', () => {
    expect(filterRecipesByReview([], true)).toEqual([])
    expect(filterRecipesByReview(null, true)).toEqual([])
    expect(filterRecipesByReview(undefined, false)).toEqual([])
  })
})

describe('recipeHasLegacyMarkdown', () => {
  it('非空原文才显示对比面板', () => {
    expect(recipeHasLegacyMarkdown({ legacy_markdown: '面粉 200g' })).toBe(true)
    expect(recipeHasLegacyMarkdown({ legacy_markdown: '' })).toBe(false)
    expect(recipeHasLegacyMarkdown({ legacy_markdown: null })).toBe(false)
    expect(recipeHasLegacyMarkdown({})).toBe(false)
  })
})
