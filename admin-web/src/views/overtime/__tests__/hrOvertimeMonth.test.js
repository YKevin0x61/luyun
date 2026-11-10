import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

const view = read('../OvertimeReviewView.vue')

/** 管理端「加班费」那一块（票 03）：口径全在服务端，页面只负责摆出来。
 *
 *  这里钉的是三件在页面上说得清、而服务端测试看不到的事：账单读的是按月的那个端点、
 *  读失败时只让这一块报错（审批队列还能用）、「算不出来」不写成「0 元」。
 */
describe('加班费账单（票 03）', () => {
  it('账单按月读 `/api/overtime/admin/month`，月份跟着台账那个筛选走', () => {
    expect(view).toMatch(/month: '\/api\/overtime\/admin\/month'/)
    expect(view).toMatch(/api\.get\(endpoints\.value\.month, monthParams\(\)\)/)
    expect(view).toMatch(/return month\.value \? \{ month: month\.value \} : undefined/)
  })

  it('账单读不出来时只让这一块报错，不整页变灰', () => {
    expect(view).toMatch(/statsError\.value = err\.message \|\| '这个月的加班费没读出来'/)
    expect(view).toMatch(/stats\.value = null/)
  })

  it('没有快照的人写「底薪待补」，不写成 0 元', () => {
    expect(view).toMatch(/row\.amount === null \? '底薪待补'/)
  })

  it('合计行摆服务端给的和，没算出来的人另有计数', () => {
    expect(view).toMatch(/stats\.total\.amount/)
    expect(view).toMatch(/stats\.total\.unpriced/)
  })
})

// B 组（2026-11-09）：批准是落账动作，原来点一下直接 POST（店长侧又看不到台账、没有
// 作废可回退）；±半小时步进在 390 上只有 32px。两条都按源码钉住。
describe('批准二次确认与手机步进（B 组 2026-11-09）', () => {
  it('批准先弹确认框，确认后才发请求（加班与存假抵扣两条队列）', () => {
    expect(view).toMatch(/@click="startApprove\('entry', entry\)"/)
    expect(view).toMatch(/@click="startApprove\('deduction', item\)"/)
    expect(view).toMatch(/function startApprove\(kind, item\)/)
    expect(view).toMatch(/function confirmApprove\(\)/)
    expect(view).toMatch(/<ConfirmDialog\s+v-if="approveTarget"/)
    expect(view).toMatch(/@confirm="confirmApprove"/)
  })

  it('手机档 ± 半小时按钮 44×44（桌面仍紧凑 32）', () => {
    expect(view).toMatch(/@media \(max-width: 720px\) \{\s*\.oStep \{ width: var\(--tap\); height: var\(--tap\); font-size: 19px; \}/)
  })
})
