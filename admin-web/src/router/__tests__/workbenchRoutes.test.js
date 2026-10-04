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

// 票 05：工作台的十一条页面按「人事 / 现场」两组落位（票 04 之前那批平铺地址随这一票
// 作废 —— 不留别名、不做重定向、自然 404；`?next=` 里的老地址由 `utils/loginNext.js`
// 在入口换成新地址）。更早的 `/hygiene/*`、`/scheduling*`、`/staff/*` 在票 03/04 已收口。
const HR_PAGES = [
  ['/workbench/hr/calendar', 'SchedulingCalendarView.vue'],
  ['/workbench/hr/inbox', 'SchedulingInboxView.vue'],
  ['/workbench/hr/shifts', 'SchedulingShiftsView.vue'],
  ['/workbench/hr/roster', 'HygieneRosterView.vue'],
]

const FLOOR_PAGES = [
  ['/workbench/floor/zones', 'HygieneZonesView.vue'],
  ['/workbench/floor/daily', 'HygieneDailyView.vue'],
  ['/workbench/floor/attire', 'HygieneAttireView.vue'],
  ['/workbench/floor/deep-clean', 'HygieneDeepCleanView.vue'],
  ['/workbench/floor/fix', 'HygieneFixView.vue'],
  ['/workbench/floor/boards', 'HygieneBoardsView.vue'],
  ['/workbench/floor/data', 'HygieneDataView.vue'],
]

// 工作台「后勤」那一组里**带工作台导航**的页面（票 07 的配方列表 / 管理，票 08 的备货
// 计划）；组里的沉浸页（阅读 / 打印 / 印码）不套外壳，不在这一份里。
const KITCHEN_SHELL_PAGES = [
  ['/workbench/kitchen/recipe', 'RecipeStationsView.vue'],
  ['/workbench/kitchen/recipe/manage', 'RecipeManageView.vue'],
  // 票 08：备货计划从管理后台的 `/prep-plan` 搬进后勤组，与配方列表同一个形态。
  ['/workbench/kitchen/prep-plan', 'PrepPlanView.vue'],
]

// 后勤组的沉浸页（票 07）：不套外壳，各挂自己那一行。
const KITCHEN_IMMERSIVE_PAGES = [
  ['/workbench/kitchen/recipe/detail', 'RecipeDetailView.vue'],
  ['/workbench/kitchen/recipe/print', 'RecipePrintView.vue'],
  ['/workbench/kitchen/recipe/qr', 'RecipeQrView.vue'],
]

const PAGES = [
  ...HR_PAGES,
  ...FLOOR_PAGES,
  ['/workbench/forbidden', 'ForbiddenView.vue'],
  ...KITCHEN_SHELL_PAGES,
  ...KITCHEN_IMMERSIVE_PAGES,
]

// 工作台「我的」那一组（员工端）：套 `WorkbenchLayout`（工作台外壳），页面本体在子记录里。
const STAFF_PAGES = [
  ['/workbench/me/today', 'TodayView.vue'],
  ['/workbench/me/month', 'TodayMonthView.vue'],
  ['/workbench/me/clean', 'HygieneHomeView.vue'],
]

describe('工作台路由（票 05 按分组落位之后）', () => {
  it('工作台内页各挂在自己那条 /workbench 路径上', () => {
    for (const [path, view] of PAGES) {
      const line = lineFor(path)
      expect(line, `${path} 没注册`).toBeTruthy()
      expect(line).toContain(view)
    }
  })

  it('人事四页走人事壳、现场七页走现场壳、后勤的列表 / 管理 / 备货计划走工作台外壳', () => {
    for (const [path] of HR_PAGES) {
      expect(lineFor(path), `${path} 该走人事壳`).toContain('workbenchHrPage')
    }
    for (const [path] of FLOOR_PAGES) {
      expect(lineFor(path), `${path} 该走现场壳`).toContain('hygieneAdminPage')
    }
    for (const [path] of KITCHEN_SHELL_PAGES) {
      expect(lineFor(path), `${path} 该走工作台外壳`).toContain('workbenchKitchenPage')
    }
    // 沉浸页不套外壳（票 07 的规矩）：它们是直接写的路由对象，不走任何工厂。
    for (const [path] of KITCHEN_IMMERSIVE_PAGES) {
      expect(lineFor(path), `${path} 该是沉浸页`).not.toContain('Page(')
    }
  })

  it('员工三页套同一个工作台外壳（导航按身份渲染），自己不再是一整页', () => {
    const layout = readFileSync(join(here, '../../views/workbench/WorkbenchLayout.vue'), 'utf8')
    for (const [path, view] of STAFF_PAGES) {
      const line = lineFor(path)
      expect(line, `${path} 没注册`).toBeTruthy()
      expect(line).toContain(view)
      expect(line).toContain('workbenchStaffPage')
    }
    expect(router).toMatch(/const WorkbenchLayout = \(\) => import\('\.\.\/views\/workbench\/WorkbenchLayout\.vue'\)/)
    expect(layout).toMatch(/workbenchNavFor/)
    expect(layout).toMatch(/<router-view \/>/)
  })

  it('子应用根 /workbench 就是首页（票 06），套工作台外壳、本体挂在空路径子记录上', () => {
    // 票 01：独立外壳标记从页面清单派生（`meta: pageMeta('/workbench')`），不再写死在这里。
    expect(router).toMatch(
      /path: '\/workbench',\n\s+component: WorkbenchLayout,\n\s+meta: pageMeta\('\/workbench'\),/,
    )
    expect(router).toMatch(
      /path: '', name: 'workbench', component: \(\) => import\('\.\.\/views\/workbench\/WorkbenchHomeView\.vue'\)/,
    )
    // 月历仍自己占一行（人事组的落点），但**不再兼职当首页**。
    expect(lineFor('/workbench/hr/calendar')).toContain('SchedulingCalendarView.vue')
    expect(lineFor('/workbench')).not.toContain('SchedulingCalendarView.vue')
  })

  it('旧地址一条都不留（不留别名、不留重定向）', () => {
    // 路径写成字面量（或没有插值的模板）是契约测试的前提（`tests/test_spa_page_routes.py`
    // 解析路由源码），所以这里也按字面查：带着引号的旧路径一旦回来，就是"又长出了第二套地址"。
    expect(router).not.toMatch(/'\/hygiene\//)
    expect(router).not.toMatch(/'\/scheduling'/)
    expect(router).not.toMatch(/'\/scheduling\//)
    // 票 03：员工三页搬进工作台，`/staff/*` 与裸 `/staff` 一起作废 —— 同样不留别名。
    expect(router).not.toMatch(/'\/staff\//)
    expect(router).not.toMatch(/'\/staff'/)
    // 票 05：票 04 那批**平铺**的工作台地址（重排成 hr / floor 两组之前的正式地址）。
    for (const flat of [
      "'/workbench/inbox'", "'/workbench/shifts'", "'/workbench/roster'", "'/workbench/zones'",
      "'/workbench/daily'", "'/workbench/attire'", "'/workbench/deep-clean'", "'/workbench/fix'",
      "'/workbench/boards'", "'/workbench/data'",
    ]) {
      expect(router, `${flat} 又回到路由里了`).not.toContain(flat)
    }
    // 票 07 / 08：配方与备货计划的老地址（独立域 `/recipe*`、管理后台的 `/prep-plan`）。
    // 带着引号查 `/prep-plan` 会连新地址一起命中 —— 所以要查的是**老写法自身**：
    // `path: '/prep-plan'` 与 `to="/prep-plan"` 两种注册写法。
    for (const old of ["path: '/prep-plan'", 'to="/prep-plan"', "'/recipe'", "'/recipe/detail'"]) {
      expect(router, `${old} 又回到路由里了`).not.toContain(old)
    }
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
