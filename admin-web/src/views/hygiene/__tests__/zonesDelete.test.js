import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const zones = readFileSync(join(here, '../HygieneZonesView.vue'), 'utf8')
// 清单整块 2026-10-19 抽进组件：删检查项随它搬走，删工作区仍在页面。
const items = readFileSync(join(here, '../../../components/hygiene/HygieneZoneItemsPanel.vue'), 'utf8')
const home = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')

describe('admin can delete a zone or daily item with confirm', () => {
  it('calls DELETE after a Chinese confirm and says the whole history goes away', () => {
    // 2026-10-26 用户裁定「对齐承诺」：删区/删检查项实际都是整区/整项清空（含已通过的
    // 历史实拍与红黑榜事件），确认文案必须如实说明，不能再承诺"只删进行中"。
    expect(zones).toMatch(/ConfirmDialog/)
    expect(zones).toMatch(/删除卫生工作区/)
    expect(zones).toMatch(/全部日常记录（含已验收的历史照片与红黑榜事件）和全部整改单/)
    expect(zones).toMatch(/全部日常记录（含历史照片）和全部整改单，删了不能恢复/)
    expect(items).toMatch(/删除日常检查项/)
    expect(items).toMatch(/该项全部记录（含历史照片），删了不能恢复/)
    expect(zones).toMatch(/api\.delete\(`\/api\/hygiene\/admin\/zones\/\$\{zone\.id\}`\)/)
    expect(items).toMatch(/api\.delete\(`\/api\/hygiene\/admin\/items\/\$\{item\.id\}`\)/)
    expect(zones).toMatch(/aria-label="`删除卫生工作区 \$\{zone\.name\}`"/)
    expect(items).toMatch(/aria-label="`删除日常检查项 \$\{item\.name\}`"/)
  })
})

describe('staff-phone has no delete for zones or items', () => {
  it('does not expose admin delete routes on the staff home', () => {
    expect(home).not.toMatch(/\/api\/hygiene\/admin\/zones/)
    expect(home).not.toMatch(/\/api\/hygiene\/admin\/items/)
    expect(home).not.toMatch(/deleteZone/)
    expect(home).not.toMatch(/deleteItem/)
  })
})
