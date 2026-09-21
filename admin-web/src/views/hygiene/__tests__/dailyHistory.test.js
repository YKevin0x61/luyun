import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { dailyCaptureUrl, frozenStandardUrl } from '../../../utils/hygieneMarkup'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

const view = read('../HygieneDailyView.vue')

describe('日常验收按营业日回看 (ADR-0088)', () => {
  it('不带日期时仍是当天队列，接口口径不变', () => {
    expect(view).toMatch(/api\.get\('\/api\/hygiene\/admin\/daily-queue', \{\s*date: businessDate\.value \|\| undefined,/)
    expect(view).toMatch(/isToday\.value = data\.is_today !== false/)
  })

  it('当天只列待验收，历史回看列当天全部检查项', () => {
    expect(view).toMatch(/rows\.value\.filter\(\(row\) => row\.status === '待验收'\)/)
    expect(view).toMatch(/isToday\.value \? pendingRows\.value : rows\.value/)
  })

  it('历史营业日只有回看：通过/驳回按钮要求今天且待验收', () => {
    expect(view).toMatch(/v-if="isToday && selected\.status === '待验收'"/)
    expect(view).toMatch(/历史回看：这一天不能补验收/)
    expect(view).toMatch(/不能通过或驳回/)
  })

  it('对照与实拍都按选中的营业日取图，并容忍已被清理的照片', () => {
    expect(view).toMatch(/frozenStandardUrl\('admin', selected, 'preview', reviewDate\)/)
    expect(view).toMatch(/dailyCaptureUrl\('admin', selected, 'preview', reviewDate\)/)
    expect(view).toMatch(/review\.standard_available \?/)
    expect(view).toMatch(/review\.capture_available \?/)
    expect(view).toMatch(/已经被「数据与照片」清理掉了/)
  })

  it('404 是「那天没交照片」，不是故障', () => {
    expect(view).toMatch(/if \(err\.status !== 404\) throw err/)
    expect(view).toMatch(/reviewMissing\.value = true/)
  })

  it('提供日期选择与一键回到今天', () => {
    expect(view).toMatch(/LuyunDatePicker/)
    expect(view).toMatch(/aria-label="日常验收营业日"/)
    expect(view).toMatch(/@click="goToday"/)
  })
})

describe('日常图片 URL 的营业日参数', () => {
  const row = { item_id: 7, shift: '白班', capture_id: 'abc', frozen_standard_id: 3 }

  it('管理端带上营业日，历史回看才取得到那一天的图', () => {
    expect(dailyCaptureUrl('admin', row, 'preview', '2026-09-01')).toBe(
      '/api/hygiene/admin/daily/7/capture?shift=%E7%99%BD%E7%8F%AD&v=abc&date=2026-09-01&variant=preview',
    )
    expect(frozenStandardUrl('admin', row, 'preview', '2026-09-01')).toBe(
      '/api/hygiene/admin/daily/7/frozen-standard?shift=%E7%99%BD%E7%8F%AD&v=3&date=2026-09-01&variant=preview',
    )
  })

  it('不给营业日时 URL 与从前一致（当天口径）', () => {
    expect(dailyCaptureUrl('admin', row, 'preview')).toBe(
      '/api/hygiene/admin/daily/7/capture?shift=%E7%99%BD%E7%8F%AD&v=abc&variant=preview',
    )
  })

  it('员工端不带营业日：员工只有当天', () => {
    expect(dailyCaptureUrl('staff', row, 'preview', '2026-09-01')).toBe(
      '/api/hygiene/staff/daily/7/capture?shift=%E7%99%BD%E7%8F%AD&v=abc&variant=preview',
    )
  })
})
