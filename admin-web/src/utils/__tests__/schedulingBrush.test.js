import { describe, expect, it } from 'vitest'

import { BRUSH_REST, BRUSH_SHIFT, brushPayload, brushReady, canPaintOn } from '../schedulingBrush'

describe('周表笔刷：笔 → 落库 payload', () => {
  it('班次笔：带工作区就是那一天的区，留空就是「跟固定区」', () => {
    expect(brushPayload({ kind: BRUSH_SHIFT, shiftId: 3, zoneId: '5' }))
      .toEqual({ shift_id: 3, zone_id: 5 })
    // 下拉给的是字符串 id，写进库要数字（跟抽屉里那条路一致）。
    expect(brushPayload({ kind: BRUSH_SHIFT, shiftId: '3', zoneId: '' }))
      .toEqual({ shift_id: 3, zone_id: null })
    expect(brushPayload({ kind: BRUSH_SHIFT, shiftId: 3, zoneId: null }))
      .toEqual({ shift_id: 3, zone_id: null })
  })

  it('休笔：只发 is_rest，不带班次也不带区', () => {
    expect(brushPayload({ kind: BRUSH_REST, shiftId: 3, zoneId: '5' })).toEqual({ is_rest: true })
  })

  it('笔不完整就不发请求（返回 null，由调用方说一句）', () => {
    expect(brushReady({ kind: BRUSH_SHIFT, shiftId: null })).toBe(false)
    expect(brushPayload({ kind: BRUSH_SHIFT, shiftId: null })).toBeNull()
    expect(brushPayload({ kind: BRUSH_SHIFT, shiftId: undefined })).toBeNull()
    expect(brushPayload({ kind: BRUSH_SHIFT, shiftId: '' })).toBeNull()
    expect(brushPayload(null)).toBeNull()
    // 休这笔不需要班次。
    expect(brushReady({ kind: BRUSH_REST, shiftId: null })).toBe(true)
  })
})

describe('周表笔刷：哪些格子能刷', () => {
  it('今天与以后能刷，昨天不能（服务端也会拒 past_day）', () => {
    expect(canPaintOn('2026-10-04', '2026-10-04')).toBe(true)
    expect(canPaintOn('2026-10-05', '2026-10-04')).toBe(true)
    expect(canPaintOn('2026-10-03', '2026-10-04')).toBe(false)
  })

  it('缺一边就放行：拦不住的交给服务端，不在客户端瞎猜', () => {
    expect(canPaintOn('', '2026-10-04')).toBe(true)
    expect(canPaintOn('2026-10-04', '')).toBe(true)
    expect(canPaintOn(undefined, undefined)).toBe(true)
  })
})
