import { describe, expect, it } from 'vitest'
import { PAGE_ROUTES, pageMeta, pageRow } from '../../router/pageRoutes.js'
import {
  WORKBENCH_FIELD_HOME,
  WORKBENCH_HR_HOME,
  WORKBENCH_TITLE,
  workbenchDocumentTitle,
} from '../workbenchCopy.js'
import {
  WORKBENCH_NAV_GROUPS,
  workbenchAudienceFor,
  workbenchGroupOf,
  workbenchNavFor,
  workbenchPagesOf,
} from '../workbenchNav.js'
import { PREP_PLAN_PATH } from '../prepPlanPaths.js'

// 票 04：导航的判据从「这一页的身份」换成「**此刻的身份**」，于是工作台身份那两档
// （`super` / `staff`）要先映射成清单里的身份词（`admin` / `staff`）。
// 票 05：工作台的两组（人事 / 现场）落位 —— 组表、落点、组里的页都从页面清单派生。
// 这里压的是映射、分组与过滤本身；「顶上那条栏跟着换」在 `workbenchShell.test.js` 里按渲染断言。

describe('工作台身份 → 清单身份', () => {
  it('两档各映射到一边，认不出的身份给 null（fail-closed，不当作管理员）', () => {
    expect(workbenchAudienceFor('super')).toBe('admin')
    expect(workbenchAudienceFor('staff')).toBe('staff')
    expect(workbenchAudienceFor(null)).toBeNull()
    expect(workbenchAudienceFor(undefined)).toBeNull()
    expect(workbenchAudienceFor('admin')).toBeNull()
  })
})

describe('工作台的组（票 05：人事 / 卫生；票 06 加上首页；票 07 加上产品；2026-10-05 加上员工卫生）', () => {
  it('六组：今天、人事、卫生、卫生、产品、我的 —— 两个「卫生」是同一批活的两张面', () => {
    expect(WORKBENCH_NAV_GROUPS.map((group) => group.key)).toEqual([
      'home', 'hr', 'floor', 'hygiene', 'kitchen', 'me',
    ])
    // 组名 2026-10-08 由用户裁定：`floor` 从「现场」改成「卫生」。它与员工端那一格
    // （`hygiene`）同名是**有意的** —— 两个身份做的是同一批卫生活，只是各自的面不同；
    // 可见性由落点那一页的 `audience` 决定，每次只有一个身份在渲染，界面上不会同时出现
    // 两个「卫生」（店长看到 `floor`、员工看到 `hygiene`）。
    expect(WORKBENCH_NAV_GROUPS.map((group) => group.label)).toEqual([
      '今天', '人事', '卫生', '卫生', '产品', '我的',
    ])
    // 第一格是子应用根（首页），不是某一组的专页。
    expect(WORKBENCH_NAV_GROUPS[0].to).toBe('/workbench')
  })

  it('每组的落点是清单里真实的一页，且那一页就属于这一组', () => {
    for (const group of WORKBENCH_NAV_GROUPS) {
      const row = pageRow(group.to)
      expect(row, `${group.key} 的落点 ${group.to} 不在页面清单里`).toBeTruthy()
      expect(row.group, `${group.key} 的落点落在别组`).toBe(group.key)
    }
  })

  it('页面标题的写法只写一次：`<页面名> · 工作台`，页面名就是「工作台」时不叠', () => {
    expect(workbenchDocumentTitle('花名册')).toBe('花名册 · 工作台')
    expect(workbenchDocumentTitle(' 排班月历 ')).toBe('排班月历 · 工作台')
    // 子应用根那一页的名字就是「工作台」（票 06 起是首页）：不写成「工作台 · 工作台」。
    expect(workbenchDocumentTitle(WORKBENCH_TITLE)).toBe('工作台')
    expect(workbenchDocumentTitle('')).toBe('工作台')
    expect(workbenchDocumentTitle(null)).toBe('工作台')
  })

  it('人事落点是排班月历、现场落点是日常验收、后勤落点是配方列表（常量只写一次）', () => {
    // 两个壳（`SchedulingLayout` / `HygieneAdminLayout`）与页内跳转都引这两个常量，
    // 谁都不许再写一遍字面量。后勤那一格的落点是清单里那一条（票 07）。
    expect(WORKBENCH_HR_HOME).toBe('/workbench/hr/calendar')
    expect(WORKBENCH_FIELD_HOME).toBe('/workbench/floor/daily')
    expect(WORKBENCH_NAV_GROUPS.find((group) => group.key === 'hr').to).toBe(WORKBENCH_HR_HOME)
    expect(WORKBENCH_NAV_GROUPS.find((group) => group.key === 'floor').to).toBe(WORKBENCH_FIELD_HOME)
    expect(WORKBENCH_NAV_GROUPS.find((group) => group.key === 'kitchen').to)
      .toBe('/workbench/kitchen/recipe')
  })

  it('组里的页从清单派生（人事六页、现场八页，顺序照清单）', () => {
    expect(workbenchPagesOf('hr').map((page) => page.path)).toEqual([
      '/workbench/hr/calendar',
      '/workbench/hr/inbox',
      '/workbench/hr/shifts',
      '/workbench/hr/roster',
      // 人事提醒（overtime-and-reminders 票 05）：工龄奖该调名单 + 档案待补。
      '/workbench/hr/reminders',
      // 加班统计（overtime-and-reminders 票 02）：超管的审批台。
      '/workbench/hr/overtime',
    ])
    expect(workbenchPagesOf('floor').map((page) => page.path)).toEqual([
      '/workbench/floor/zones',
      '/workbench/floor/daily',
      '/workbench/floor/attire',
      '/workbench/floor/deep-clean',
      '/workbench/floor/fix',
      '/workbench/floor/boards',
      '/workbench/floor/data',
      // 卫生趋势（2026-10-05 用户裁定）：现场组的第八页，排在同组最后。
      '/workbench/floor/trend',
    ])
    // 标题也从清单来：外壳的导航与页签不另抄一份名字。
    expect(workbenchPagesOf('hr').map((page) => page.title)).toEqual([
      '排班月历', '排班待办', '班次表', '花名册', '人事提醒', '加班统计',
    ])
    // 认不出的组给空表（不猜、也不兜底成一整份清单）。
    expect(workbenchPagesOf('nope')).toEqual([])
  })

  it('十四页正好落在人事 / 现场两组里，平铺那批地址一条都不在清单里', () => {
    const grouped = PAGE_ROUTES.filter((row) => row.group === 'hr' || row.group === 'floor')
    expect(grouped).toHaveLength(14)
    // 留一半最坏：「点得进去、刷新 404」。
    for (const old of [
      '/workbench/inbox', '/workbench/shifts', '/workbench/roster', '/workbench/zones',
      '/workbench/daily', '/workbench/deep-clean', '/workbench/fix', '/workbench/boards',
      '/workbench/data', '/workbench/attire',
    ]) {
      expect(pageRow(old), `${old} 又回到清单里了`).toBe(null)
    }
  })

  it('产品组：清单里是配方五页 + 备货计划，导航入口只给能当入口的那四页', () => {
    // 页面清单仍是完整的六条 —— 路由、页面墙、页面标题都从它来。
    expect(PAGE_ROUTES.filter((row) => row.group === 'kitchen').map((row) => row.path)).toEqual([
      '/workbench/kitchen/recipe',
      '/workbench/kitchen/recipe/detail',
      '/workbench/kitchen/recipe/print',
      '/workbench/kitchen/recipe/qr',
      '/workbench/kitchen/recipe/manage',
      // 票 08：备货计划从 `/prep-plan` 搬进这一组 —— 换位置、统一导航，读写门不动。
      '/workbench/kitchen/prep-plan',
    ])
    // **导航入口**是组表里手写的那四页（2026-10-08）：清单里的「配方详情」（要带 `?slug=`）
    // 与「配方打印」（A4 预览）是**从列表点进去**的功能页 —— 当成一级入口就是两扇点不通的
    // 死门（页头那个下拉里实测派生出来是六项，其中两项点了进不去）。
    expect(workbenchPagesOf('kitchen').map((page) => page.path)).toEqual([
      '/workbench/kitchen/recipe',
      '/workbench/kitchen/recipe/qr',
      '/workbench/kitchen/recipe/manage',
      '/workbench/kitchen/prep-plan',
    ])
    // 名单里只写路径，标题仍从清单取（一处定义）。
    expect(workbenchPagesOf('kitchen').map((page) => page.title)).toEqual([
      '选择岗位', '岗位二维码', '配方管理', '备货计划',
    ])
    for (const old of [
      '/recipe', '/recipe/detail', '/recipe/print', '/recipe/qr', '/recipe/manage',
      // 票 08：备货计划的老地址（自然 404，不给别名、不做重定向）。
      '/prep-plan',
    ]) {
      expect(pageRow(old), `${old} 又回到清单里了`).toBe(null)
    }
  })
})

describe('按身份过滤导航', () => {
  it('认不出身份时一格都不渲染（没有点不通的入口）', () => {
    expect(workbenchNavFor(null)).toEqual([])
    expect(workbenchNavFor(undefined)).toEqual([])
    expect(workbenchNavFor('')).toEqual([])
  })

  it('每一格的可见性来自它指向那一页的 audience（不另写一份名单）', () => {
    for (const group of WORKBENCH_NAV_GROUPS) {
      const { audience } = pageMeta(group.to)
      const shown = workbenchNavFor(audience === 'admin' ? 'super' : 'staff')
      expect(shown.map((item) => item.key), `${group.key} 的可见性`).toContain(group.key)
      if (audience === 'admin' || audience === 'staff') {
        const other = audience === 'admin' ? 'staff' : 'super'
        expect(workbenchNavFor(other).map((item) => item.key), `${group.key} 藏给另一边`).not.toContain(group.key)
      }
    }
  })

  it('后勤那一格是「一组两门」：两条链接各自按落点那一页的 audience 过滤', () => {
    // 票 08 的形态：组表里 `links` 是**一组多条目**的入口，不是把同一组拆成两格
    // （拆成两格的话 `workbenchGroupOf` 会让两格同时高亮）。每条链接的可见性仍只看
    // 它自己落点那一页的身份 —— 不另写一份名单。
    const kitchen = WORKBENCH_NAV_GROUPS.find((group) => group.key === 'kitchen')
    expect(kitchen.links.map((link) => link.to)).toEqual([
      '/workbench/kitchen/recipe',
      PREP_PLAN_PATH,
    ])
    for (const link of kitchen.links) {
      const row = pageRow(link.to)
      expect(row, `后勤的落点 ${link.to} 不在页面清单里`).toBeTruthy()
      expect(row.group, `后勤的落点 ${link.to} 落在别组`).toBe('kitchen')
    }
    // 两页都是 `both` —— 员工这一档两条都看得见（票面验收：员工导航加上备货计划）。
    expect(pageMeta(PREP_PLAN_PATH).audience).toBe('both')

    const toOf = (identity) => workbenchNavFor(identity)
      .filter((item) => item.key === 'kitchen')
      .map((item) => item.to)
    expect(toOf('super')).toEqual(['/workbench/kitchen/recipe', PREP_PLAN_PATH])
    expect(toOf('staff')).toEqual(['/workbench/kitchen/recipe', PREP_PLAN_PATH])
  })

  it('店长这一档：今天 / 人事 / 现场 / 后勤四格；员工那一档：今天 / 后勤 / 我的', () => {
    // 员工那一档就是 spec 故事 8 那三项（配方与备货计划只读），外加两档都看得见的首页「今天」。
    // 按**格**（组）断言：后勤那一格现在是两条链接，数格子才是"一格一组"的那条规矩。
    const keysOf = (identity) => [
      ...new Set(workbenchNavFor(identity).map((item) => item.key)),
    ]
    expect(keysOf('super')).toEqual(['home', 'hr', 'floor', 'kitchen'])
    // 员工那一档多了「卫生」（2026-10-05 裁定的重构：他们每天要做的活给一个一级入口），
    // 而店长的「人事 / 现场」两格对他不可见 —— 卫生那一格反过来只对员工可见。
    expect(keysOf('staff')).toEqual(['home', 'hygiene', 'kitchen', 'me'])
    // 首页那一页是 `both`（票 06）：两档都进得去，过滤天然放行。
    expect(pageMeta('/workbench').audience).toBe('both')
    expect(pageMeta(WORKBENCH_HR_HOME).audience).toBe('admin')
    expect(pageMeta(WORKBENCH_FIELD_HOME).audience).toBe('admin')
    // 后勤的落点是配方列表：`both` —— 员工也要看得到配方这一项（票面验收）。
    expect(pageMeta('/workbench/kitchen/recipe').audience).toBe('both')
    expect(pageMeta('/workbench/kitchen/recipe/manage').audience).toBe('admin')
    expect(pageMeta('/workbench/me/today').audience).toBe('staff')
  })
})

describe('高亮跟着新分组走', () => {
  it('整组下的任意一页都算在那一格上', () => {
    expect(workbenchGroupOf('/workbench/me/month')).toBe('me')
    // 卫生那一页 2026-10-05 从「我的」组挪进了自己的「卫生」组（它有了一级入口，
    // 再挂在「我的」组上会让两格同时亮）。
    expect(workbenchGroupOf('/workbench/me/clean')).toBe('hygiene')
    expect(workbenchGroupOf('/workbench/hr/calendar')).toBe('hr')
    expect(workbenchGroupOf('/workbench/hr/roster')).toBe('hr')
    expect(workbenchGroupOf('/workbench/floor/daily')).toBe('floor')
    expect(workbenchGroupOf('/workbench/floor/data')).toBe('floor')
    // 票 07：后勤组里的沉浸页（阅读 / 打印 / 印码）也算在「后勤」那一格上。
    expect(workbenchGroupOf('/workbench/kitchen/recipe/detail')).toBe('kitchen')
    expect(workbenchGroupOf('/workbench/kitchen/recipe/qr')).toBe('kitchen')
    // 票 08：站在备货计划上时，「后勤」那一格仍然亮 —— 判据是页面的 `group`，
    // 不是链接自己的路径（后勤那一格现在指着配方列表）。
    expect(workbenchGroupOf(PREP_PLAN_PATH)).toBe('kitchen')
    // 子应用根（票 06 的首页那一组）不属于人事 / 现场任何一格。
    expect(workbenchGroupOf('/workbench')).toBe('home')
    // 不在清单里的路径（单测里的临时路由）没有组，也就没有高亮。
    expect(workbenchGroupOf('/nowhere')).toBeNull()
  })
})
