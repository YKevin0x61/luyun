import { describe, expect, it } from 'vitest'
import { normalizeDishName, resolveSubCategory } from '../salesReportText.js'

describe('normalizeDishName', () => {
  it('去除外卖前缀与数量单位后缀', () => {
    expect(normalizeDishName('(外卖)椒盐龙虾(2只)')).toBe('椒盐龙虾')
  })

  it('去除份量前缀', () => {
    expect(normalizeDishName('(小份)潮汕鱼蛋')).toBe('潮汕鱼蛋')
  })

  it('无装饰的菜名保持不变', () => {
    expect(normalizeDishName('白切鸡')).toBe('白切鸡')
  })
})

describe('resolveSubCategory', () => {
  it('优先使用 sub_category', () => {
    expect(resolveSubCategory({ sub_category: '半成品', category: '原料' })).toBe('半成品')
  })

  it('sub_category 缺失时回退为 category', () => {
    expect(resolveSubCategory({ category: '原料' })).toBe('原料')
  })
})
