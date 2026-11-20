import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const home = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')

// 真机走查：首件卡片拿走一项之后，事实胶囊写「超时 11」、列表标题只有「已超时 10」，
// 两个措辞不同、数字不同的「超时」同屏出现，员工会以为数据算错了。
describe('员工卫生页「超时」口径（首件卡片拿走一项，标题与列表不能差 1）', () => {
  it('事实胶囊与分组标题都叫「已超时」，标题数整桶并注明卡片那一项', () => {
    const facts = home.slice(
      home.indexOf('hy-work-facts'),
      home.indexOf('hy-staff-lead', home.indexOf('hy-work-facts')),
    )
    // 同一屏两处「超时」措辞统一，不再一个「超时」一个「已超时」。
    expect(facts).toMatch(/已超时 \{\{ queueSummary\.overdue \}\}/)
    // 分组标题数的是整桶（含首件卡片），不再只数列表里剩下的行。
    expect(home).toMatch(
      /<h2><i class="hy-wb-led sig"><\/i>\{\{ group\.label \}\} <span>\{\{ queueBucketTotal\(group\.id\) \}\}<\/span>/,
    )
    expect(home).toMatch(/含上方卡片 \{\{ queueBucketTotal\(group\.id\) - group\.rows\.length \}\} 项/)
  })

  it('事实胶囊里的「0/11」写明是已通过，不是完成进度', () => {
    const facts = home.slice(
      home.indexOf('hy-work-facts'),
      home.indexOf('hy-staff-lead', home.indexOf('hy-work-facts')),
    )
    expect(facts).toMatch(/日常 \{\{ dailyStats\.passed \}\}\/\{\{ dailyStats\.total \}\}（已通过）/)
  })
})
