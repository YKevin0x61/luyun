import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

const view = read('../HrOvertimeView.vue')

/** 管理端「加班费」那一块（票 03）：口径全在服务端，页面只负责摆出来。
 *
 *  这里钉的是三件在页面上说得清、而服务端测试看不到的事：账单读的是按月的那个端点、
 *  读失败时只让这一块报错（审批队列还能用）、「算不出来」不写成「0 元」。
 */
describe('加班费账单（票 03）', () => {
  it('账单按月读 `/api/overtime/admin/month`，月份跟着台账那个筛选走', () => {
    expect(view).toMatch(/api\.get\('\/api\/overtime\/admin\/month', params\)/)
    expect(view).toMatch(
      /const params = month\.value \? \{ month: month\.value \} : undefined/,
    )
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
