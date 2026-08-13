import { describe, expect, it } from 'vitest'
import { toastForServeBatch } from '../serveBatchToast.js'

describe('toastForServeBatch', () => {
  it('returns null when every requested 份 succeeded', () => {
    expect(toastForServeBatch({ processed: 3, requested: 3 })).toBeNull()
  })

  it('toasts partial 出餐 with processed/requested counts', () => {
    expect(toastForServeBatch({ processed: 2, requested: 5 })).toEqual({
      title: '部分出餐成功 2/5份',
      icon: 'none'
    })
  })

  it('toasts when the batch throws', () => {
    expect(
      toastForServeBatch({
        processed: 1,
        requested: 3,
        errorMessage: '网络中断'
      })
    ).toEqual({
      title: '批量出餐失败: 网络中断',
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
