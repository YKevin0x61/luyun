import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const router = readFileSync(join(here, '../index.js'), 'utf8')
const lines = router.split('\n')

function lineFor(path) {
  return lines.find((line) => line.includes(`'${path}'`))
}

// 票 04（收口）：排班 + 卫生合并成「工作台」，两组同住 `/workbench/*`，**只剩这一套**。
// 旧前缀 `/scheduling*` 与 `/hygiene/*` 已删干净、不留别名 —— 用户拍板：门店手机上
// 那些旧书签 404 可以接受（`?next=` 里的老地址由 `utils/loginNext.js` 在入口换掉）。
const PAGES = [
  ['/workbench/inbox', 'SchedulingInboxView.vue'],
  ['/workbench/shifts', 'SchedulingShiftsView.vue'],
  ['/workbench/roster', 'HygieneRosterView.vue'],
  ['/workbench/zones', 'HygieneZonesView.vue'],
  ['/workbench/daily', 'HygieneDailyView.vue'],
  ['/workbench/deep-clean', 'HygieneDeepCleanView.vue'],
  ['/workbench/fix', 'HygieneFixView.vue'],
  ['/workbench/boards', 'HygieneBoardsView.vue'],
  ['/workbench/data', 'HygieneDataView.vue'],
  ['/workbench/attire', 'HygieneAttireView.vue'],
]

describe('工作台路由（票 04 收口之后）', () => {
  it('十个内页各挂在自己那条 /workbench 路径上', () => {
    for (const [path, view] of PAGES) {
      const line = lineFor(path)
      expect(line, `${path} 没注册`).toBeTruthy()
      expect(line).toContain(view)
    }
  })

  it('首页套同一层壳、是独立页，本体挂在空路径子记录上', () => {
    // 票 01：独立外壳标记从页面清单派生（`meta: pageMeta('/workbench')`），不再写死在这里。
    expect(router).toMatch(
      /path: '\/workbench',\n\s+component: \(\) => import\('\.\.\/views\/scheduling\/SchedulingLayout\.vue'\),\n\s+meta: pageMeta\('\/workbench'\),/,
    )
    expect(router).toMatch(
      /path: '', name: 'workbench', component: \(\) => import\('\.\.\/views\/scheduling\/SchedulingCalendarView\.vue'\)/,
    )
  })

  it('旧前缀一条都不留（不留别名、不留重定向）', () => {
    // 路径写成字面量（或没有插值的模板）是契约测试的前提（`tests/test_spa_page_routes.py`
    // 解析路由源码），所以这里也按字面查：带着引号的旧路径一旦回来，就是"又长出了第二套地址"。
    expect(router).not.toMatch(/'\/hygiene\//)
    expect(router).not.toMatch(/'\/scheduling'/)
    expect(router).not.toMatch(/'\/scheduling\//)
    // 老地址靠 `?next=` 迁移，不给它留路由：留了就会跟新前缀漂成两套。
    expect(router).not.toMatch(/alias:/)
    expect(router).not.toMatch(/redirect:/)
  })

  it('路由名唯一（vue-router 名字撞了会静默覆盖）', () => {
    const names = [...router.matchAll(/name: '([a-z-]+)'/g)].map((m) => m[1])
    const duplicated = names.filter((name, index) => names.indexOf(name) !== index)
    expect(duplicated).toEqual([])
  })
})
