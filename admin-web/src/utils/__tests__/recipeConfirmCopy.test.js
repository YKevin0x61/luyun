import { describe, expect, it } from 'vitest'
import {
  deleteRecipeConfirmCopy,
  deleteStationConfirmCopy,
  discardRecipeEditsCopy,
  restoreHistoryCopy,
} from '../recipeConfirmCopy.js'

describe('deleteStationConfirmCopy', () => {
  it('写出条数和修改历史，不说条目', () => {
    const copy = deleteStationConfirmCopy({ title: '肠粉档', recipeCount: 27 })
    expect(copy.title).toBe('删除岗位「肠粉档」？')
    expect(copy.body).toBe('将删除本岗全部 27 条配方及修改历史，不可恢复。')
    expect(copy.body).not.toMatch(/条目/)
    expect(copy.confirmLabel).toBe('删除岗位')
  })

  it('缺条数按 0', () => {
    expect(deleteStationConfirmCopy({ title: '馅档' }).body).toContain('全部 0 条配方')
  })
})

describe('deleteRecipeConfirmCopy', () => {
  it('点名配方', () => {
    const copy = deleteRecipeConfirmCopy({ recipeName: '肠粉酱油' })
    expect(copy.title).toBe('删除配方「肠粉酱油」？')
    expect(copy.body).toContain('修改历史')
  })
})

describe('discardRecipeEditsCopy', () => {
  it('说明关闭不会保存', () => {
    expect(discardRecipeEditsCopy().body).toMatch(/不会保存/)
  })
})

describe('restoreHistoryCopy', () => {
  it('说明先记一笔再写回', () => {
    const copy = restoreHistoryCopy({
      recipeName: '面团',
      changedAt: '2026-08-01T00:00:00+00:00',
    })
    expect(copy.title).toContain('面团')
    expect(copy.body).toMatch(/先记入修改历史/)
    expect(copy.body).toContain('2026-08-01T00:00:00+00:00')
  })
})
