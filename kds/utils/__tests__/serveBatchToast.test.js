import { describe, expect, it } from 'vitest'
import { toastForServeBatch } from '../serveBatchToast.js'

describe('toastForServeBatch', () => {
  it('returns null when every requested 份 succeeded', () => {
    expect(toastForServeBatch({ processed: 3, requested: 3 })).toBeNull()
  })

  it('does not toast a partial 出餐 success', () => {
    expect(toastForServeBatch({ processed: 2, requested: 5 })).toBeNull()
  })

  it('toasts a whole-confirm failure, not a partial 出餐 success', () => {
    expect(
      toastForServeBatch({
        processed: 0,
        requested: 3,
        errorMessage: '出餐确认冲突'
      })
    ).toEqual({
      title: '出餐失败: 出餐确认冲突',
      icon: 'error'
    })
  })

  it('toasts when nothing was submitted', () => {
    expect(toastForServeBatch({ processed: 0, requested: 2 })).toEqual({
      title: '没有可提交的订单',
      icon: 'none'
    })
  })
})
