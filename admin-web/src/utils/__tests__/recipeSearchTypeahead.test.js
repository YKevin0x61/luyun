import { describe, expect, it } from 'vitest'
import {
  SEARCH_GROUP_ITEM_CAP,
  capGroupedSearchHits,
  flattenCappedSearchHits,
  nextTypeaheadKeyboardState,
  recipeFocusLocation,
  recipeManageEditLocation,
} from '../recipeSearchTypeahead.js'

const groups = [
  {
    station_slug: 'changfen',
    station_title: '肠粉档',
    items: [
      { recipe_id: 1, recipe_name: '肠粉酱油', section: '配方' },
      { recipe_id: 2, recipe_name: '鲜虾肠', section: '配方' },
    ],
  },
  {
    station_slug: 'shulong',
    station_title: '熟笼档',
    items: [
      { recipe_id: 10, recipe_name: '鲜虾饺', section: '配方' },
    ],
  },
]

describe('capGroupedSearchHits', () => {
  it('每组最多保留 8 条', () => {
    const many = {
      station_slug: 'changfen',
      station_title: '肠粉档',
      items: Array.from({ length: 10 }, (_, i) => ({
        recipe_id: i + 1,
        recipe_name: `条目${i + 1}`,
        section: '配方',
      })),
    }
    const capped = capGroupedSearchHits([many])
    expect(SEARCH_GROUP_ITEM_CAP).toBe(8)
    expect(capped[0].items.map((item) => item.recipe_id)).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
  })
})

describe('flattenCappedSearchHits', () => {
  it('跨岗位展平成一条列表并带上岗位字段', () => {
    expect(flattenCappedSearchHits(groups)).toEqual([
      {
        recipe_id: 1,
        recipe_name: '肠粉酱油',
        section: '配方',
        station_slug: 'changfen',
        station_title: '肠粉档',
      },
      {
        recipe_id: 2,
        recipe_name: '鲜虾肠',
        section: '配方',
        station_slug: 'changfen',
        station_title: '肠粉档',
      },
      {
        recipe_id: 10,
        recipe_name: '鲜虾饺',
        section: '配方',
        station_slug: 'shulong',
        station_title: '熟笼档',
      },
    ])
  })
})

describe('nextTypeaheadKeyboardState', () => {
  it('ArrowDown 在关闭时打开并选中第一项', () => {
    expect(nextTypeaheadKeyboardState({ open: false, activeIndex: -1, itemCount: 3 }, 'ArrowDown')).toEqual({
      open: true,
      activeIndex: 0,
      selectedIndex: null,
    })
  })

  it('ArrowDown / ArrowUp 在展平列表上循环', () => {
    expect(nextTypeaheadKeyboardState({ open: true, activeIndex: 2, itemCount: 3 }, 'ArrowDown')).toEqual({
      open: true,
      activeIndex: 0,
      selectedIndex: null,
    })
    expect(nextTypeaheadKeyboardState({ open: true, activeIndex: 0, itemCount: 3 }, 'ArrowUp')).toEqual({
      open: true,
      activeIndex: 2,
      selectedIndex: null,
    })
  })

  it('Enter 选中当前项并关闭，Esc 关闭且不导航', () => {
    expect(nextTypeaheadKeyboardState({ open: true, activeIndex: 1, itemCount: 3 }, 'Enter')).toEqual({
      open: false,
      activeIndex: 1,
      selectedIndex: 1,
    })
    expect(nextTypeaheadKeyboardState({ open: true, activeIndex: 1, itemCount: 3 }, 'Escape')).toEqual({
      open: false,
      activeIndex: -1,
      selectedIndex: null,
    })
  })
})

describe('recipeFocusLocation', () => {
  it('跳转到详情页并带 focus 参数', () => {
    expect(recipeFocusLocation('changfen', 42)).toEqual({
      path: '/recipe/detail',
      query: { slug: 'changfen', focus: '42' },
    })
  })
})

describe('recipeManageEditLocation', () => {
  it('打开管理页并带上岗位和配方 id', () => {
    expect(recipeManageEditLocation('肠粉档', 120)).toEqual({
      path: '/recipe/manage',
      query: { slug: '肠粉档', edit: '120' },
    })
  })
})
