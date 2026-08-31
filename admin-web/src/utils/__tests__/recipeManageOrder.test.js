import { describe, expect, it } from 'vitest'
import {
  DEFAULT_RECIPE_SECTION,
  RECIPE_SECTIONS,
  assignSortOrders,
  buildSectionOptions,
  canonicalizeSection,
  defaultSectionForNewRecipe,
} from '../recipeManageOrder.js'

describe('buildSectionOptions', () => {
  it('always lists the four canonical sections in order', () => {
    expect(buildSectionOptions([])).toEqual(
      RECIPE_SECTIONS.map((section) => ({ value: section, label: section })),
    )
    expect(buildSectionOptions([{ section: '粥品' }])).toEqual(buildSectionOptions())
  })
})

describe('defaultSectionForNewRecipe', () => {
  it('defaults new recipes to 配方', () => {
    expect(defaultSectionForNewRecipe()).toBe(DEFAULT_RECIPE_SECTION)
    expect(defaultSectionForNewRecipe([{ section: '出品标准' }])).toBe('配方')
  })
})

describe('canonicalizeSection', () => {
  it('maps legacy headings onto the four sections', () => {
    expect(canonicalizeSection('粥品')).toBe('配方')
    expect(canonicalizeSection('二十大招牌检核')).toBe('检核要求')
    expect(canonicalizeSection('常规检核')).toBe('检核要求')
    expect(canonicalizeSection('出品标准')).toBe('出品标准')
    expect(canonicalizeSection('食安要求')).toBe('食安要求')
  })
})

describe('assignSortOrders', () => {
  it('按视觉顺序写入从 0 起的连续唯一 sort_order', () => {
    expect(assignSortOrders([
      { id: 10, sort_order: 99 },
      { id: 20, sort_order: 0 },
      { id: 30, sort_order: 0 },
    ])).toEqual([
      { id: 10, sort_order: 0 },
      { id: 20, sort_order: 1 },
      { id: 30, sort_order: 2 },
    ])
  })

  it('空列表返回空赋值', () => {
    expect(assignSortOrders([])).toEqual([])
  })

  it('拖拽后的新顺序不产生重复排序号', () => {
    const assigned = assignSortOrders([{ id: 7 }, { id: 1 }, { id: 4 }, { id: 2 }])
    expect(assigned).toEqual([
      { id: 7, sort_order: 0 },
      { id: 1, sort_order: 1 },
      { id: 4, sort_order: 2 },
      { id: 2, sort_order: 3 },
    ])
    const orders = assigned.map((row) => row.sort_order)
    expect(new Set(orders).size).toBe(4)
  })
})
