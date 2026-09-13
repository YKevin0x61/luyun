import { describe, expect, it } from 'vitest'
import {
  MARK_KINDS,
  clamp01,
  createArrowMark,
  createCaptionMark,
  createCircleMark,
  chinaNowIso,
  dailyCaptureUrl,
  deepCleanShotUrl,
  fixOriginalUrl,
  fixReshootUrl,
  formatWatermarkTime,
  frozenStandardUrl,
  parseMarkup,
  standardImageUrl,
  teachingShotUrl,
} from '../hygieneMarkup.js'

describe('hygieneMarkup', () => {
  it('圆圈箭头批注三种标注，坐标夹在 0 到 1', () => {
    expect(MARK_KINDS).toEqual(['circle', 'arrow', 'caption'])
    expect(createCircleMark(-1, 2, 0.08)).toEqual({ kind: 'circle', x: 0, y: 1, r: 0.08 })
    expect(createArrowMark(0.2, 0.8, 0.5, 0.4)).toEqual({
      kind: 'arrow',
      x1: 0.2,
      y1: 0.8,
      x2: 0.5,
      y2: 0.4,
    })
    expect(createCaptionMark(0.5, 0.9, '  擦干净  ')).toEqual({
      kind: 'caption',
      x: 0.5,
      y: 0.9,
      text: '擦干净',
    })
    expect(clamp01('nope')).toBe(0)
  })

  it('解析标注时丢掉不明类型，坏 JSON 当空', () => {
    expect(parseMarkup('not-json')).toEqual([])
    expect(parseMarkup({ kind: 'circle' })).toEqual([])
    expect(
      parseMarkup([
        { kind: 'circle', x: 0.4, y: 0.3, r: 0.08 },
        { kind: 'polygon', x: 0 },
        { kind: 'caption', x: 0.5, y: 0.9, text: '擦干净' },
      ]),
    ).toEqual([
      { kind: 'circle', x: 0.4, y: 0.3, r: 0.08 },
      { kind: 'caption', x: 0.5, y: 0.9, text: '擦干净' },
    ])
  })

  it('标准图地址带当前版本，换图后预览不会吃到旧缓存', () => {
    expect(standardImageUrl('staff', { id: 10, current_standard_id: 3 })).toBe(
      '/api/hygiene/staff/items/10/standard?v=3',
    )
    expect(standardImageUrl('admin', { id: 10, current_standard_id: 4 })).toBe(
      '/api/hygiene/admin/items/10/standard?v=4',
    )
  })

  it('日常实拍和提交当时标准图地址带班次和版本', () => {
    const row = {
      item_id: 10,
      shift: '白班',
      capture_id: 'fake-2',
      frozen_standard_id: 7,
    }
    expect(dailyCaptureUrl('staff', row)).toBe(
      '/api/hygiene/staff/daily/10/capture?shift=%E7%99%BD%E7%8F%AD&v=fake-2',
    )
    expect(frozenStandardUrl('admin', row)).toBe(
      '/api/hygiene/admin/daily/10/frozen-standard?shift=%E7%99%BD%E7%8F%AD&v=7',
    )
    expect(formatWatermarkTime('2026-09-13T10:00:00+08:00')).toBe('2026-09-13 10:00')
    expect(chinaNowIso(new Date('2026-09-13T02:00:00.000Z'))).toBe(
      '2026-09-13T10:00:00+08:00',
    )
  })

  it('专项前后实拍地址带专项清单项和版本，不带卫生责任区', () => {
    const row = {
      item_id: 4,
      before_capture_id: 'before-1',
      after_capture_id: 'after-2',
    }
    expect(deepCleanShotUrl('staff', row, 'before')).toBe(
      '/api/hygiene/staff/deep-clean/4/before?v=before-1',
    )
    expect(deepCleanShotUrl('admin', row, 'after')).toBe(
      '/api/hygiene/admin/deep-clean/4/after?v=after-2',
    )
  })

  it('整改单原图和回拍地址带版本，不把原图叠进镜头路径', () => {
    const ticket = { id: 9, capture_id: 'open-1', reshoot_capture_id: 'reshot-2' }
    expect(fixOriginalUrl('staff', ticket)).toBe('/api/hygiene/staff/fix/9/original?v=open-1')
    expect(fixReshootUrl('admin', ticket)).toBe('/api/hygiene/admin/fix/9/reshoot?v=reshot-2')
  })

  it('教材左右图走教学接口，不复用待验收路径', () => {
    const example = { id: 4, left_capture_id: 'std-1', right_capture_id: 'shot-2' }
    expect(teachingShotUrl('staff', example, 'left')).toBe(
      '/api/hygiene/staff/teaching/4/left?v=std-1',
    )
    expect(teachingShotUrl('admin', example, 'right')).toBe(
      '/api/hygiene/admin/teaching/4/right?v=shot-2',
    )
  })
})
