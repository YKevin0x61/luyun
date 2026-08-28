import { describe, expect, it } from 'vitest'
import { NEW_SECTION_VALUE, assignSortOrders, buildSectionOptions } from '../recipeManageOrder.js'

describe('buildSectionOptions', () => {
  it('空岗位仍提供新建章节选项，且不含全局固定枚举', () => {
    expect(buildSectionOptions([])).toEqual([
      { value: NEW_SECTION_VALUE, label: '新建章节…' },
    ])
  })

  it('按首次出现顺序去重已有章节，并追加新建选项', () => {
    expect(buildSectionOptions([
      { section: '配方' },
      { section: '出品标准' },
      { section: '配方' },
      { section: '操作要点' },
    ])).toEqual([
      { value: '配方', label: '配方' },
      { value: '出品标准', label: '出品标准' },
      { value: '操作要点', label: '操作要点' },
      { value: NEW_SECTION_VALUE, label: '新建章节…' },
    ])
  })

  it('不清洗历史章节名的空格或全半角差异', () => {
    expect(buildSectionOptions([
      { section: '配方' },
      { section: '配方 ' },
      { section: '配 方' },
      { section: '配方' },
    ])).toEqual([
      { value: '配方', label: '配方' },
      { value: '配方 ', label: '配方 ' },
      { value: '配 方', label: '配 方' },
      { value: NEW_SECTION_VALUE, label: '新建章节…' },
    ])
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
