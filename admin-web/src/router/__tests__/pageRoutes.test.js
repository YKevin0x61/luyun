// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import {
  PAGE_ROUTES,
  pageMeta,
  pageRow,
  pageTitle,
  skipsAdminLoginRedirect,
} from '../pageRoutes.js'

// 票 01：页面清单（`router/pageRoutes.json`）是唯一来源。这里钉两件事：
// 1. 表本身合法（六个字段齐全、取值在枚举内、路径唯一）；
// 2. 真正注册的 vue-router 路由确实**从表派生** —— 每一条注册路径都在表里、
//    表里每一页都注册了，且每条路由的「独立外壳 / 免登录」标记等于表里的值。
//
// 断言的是行为（注册出来的路由表长什么样），不是源码写法：router 怎么改结构都行，
// 只要还是从这一张表派生。

const GROUPS = ['home', 'hr', 'floor', 'hygiene', 'kitchen', 'me', 'system', 'entry']
const AUDIENCES = ['admin', 'staff', 'both']

/** 每个用例都换一份崭新的 router 模块（它是单例，`createWebHistory` 也只该建一次）。 */
async function freshRouter() {
  vi.resetModules()
  const mod = await import('../index.js')
  return mod.default
}

describe('页面清单（唯一来源）', () => {
  it('每条都带齐六个字段，取值在枚举内', () => {
    expect(PAGE_ROUTES.length).toBeGreaterThan(20)
    for (const row of PAGE_ROUTES) {
      expect(row.path.startsWith('/'), row.path).toBe(true)
      expect(typeof row.title, row.path).toBe('string')
      expect(row.title.trim(), row.path).not.toBe('')
      expect(GROUPS, row.path).toContain(row.group)
      expect(AUDIENCES, row.path).toContain(row.audience)
      expect(typeof row.standalone, row.path).toBe('boolean')
      expect(typeof row.public, row.path).toBe('boolean')
    }
  })

  it('路径唯一（含服务端别名）', () => {
    const paths = PAGE_ROUTES.map((row) => row.path)
    expect(new Set(paths).size).toBe(paths.length)
    const aliases = PAGE_ROUTES.flatMap((row) => row.aliases || [])
    expect(aliases.filter((alias) => paths.includes(alias))).toEqual([])
  })

  it('独立外壳的页面就是这些（其余页面才渲染后台导航）', () => {
    expect(PAGE_ROUTES.filter((row) => row.standalone).map((row) => row.path)).toEqual([
      '/login',
      '/register',
      '/settings',
      '/workbench',
      // 票 05：工作台按组落位 —— 人事四页（月历 / 待办 / 班次表 / 花名册）。
      '/workbench/hr/calendar',
      '/workbench/hr/inbox',
      '/workbench/hr/shifts',
      '/workbench/hr/roster',
      // 现场七页（卫生）。
      '/workbench/floor/zones',
      '/workbench/floor/daily',
      '/workbench/floor/attire',
      '/workbench/floor/deep-clean',
      '/workbench/floor/fix',
      '/workbench/floor/boards',
      '/workbench/floor/data',
      // 卫生趋势（2026-10-05 用户裁定）：现场组的第八页，与同组那七页同壳（standalone）。
      '/workbench/floor/trend',
      // 票 07：后勤组的配方五页 —— 两种外壳（带工作台导航的列表与管理，沉浸的阅读 /
      // 打印 / 印码）在这张表里都记 `standalone: true`（那条的语义是「不渲染管理后台
      // 那条导航」，不是「没有外壳」）。
      '/workbench/kitchen/recipe',
      '/workbench/kitchen/recipe/detail',
      '/workbench/kitchen/recipe/print',
      '/workbench/kitchen/recipe/qr',
      '/workbench/kitchen/recipe/manage',
      // 票 08：备货计划从管理后台的 `/prep-plan` 搬进后勤组 —— 套的是工作台外壳
      // （顶栏那条窄栏 + 按身份过滤的导航），后台那条导航不再渲染它。
      '/workbench/kitchen/prep-plan',
      // 员工三页搬进工作台的「我的」组（票 03）：套工作台外壳，后台导航照样不渲染；
      // 越权落点也是独立一页（一页说明 + 一颗按钮，不套导航）。
      '/workbench/me/today',
      '/workbench/me/month',
      // 加班与补钟（票 01）：员工自己提一笔、看自己的记录与月度净时长，同套工作台外壳。
      '/workbench/me/overtime',
      '/workbench/me/clean',
      '/workbench/forbidden',
    ])
  })

  it('免登录的页面就是这些（只剩登录页与注册页两个入口）', () => {
    // 票 03 起工作台里的页一律不是 public（工作台是「进去要登录」的页面区）：员工三页
    // 从这份名单里退出，页面壳的放行改由「任一会话有效」决定。
    // 票 07 起配方阅读面也退出（本仓唯一一次推翻既有刻意设计）：扫码看配方保留，
    // 但扫码的人先登录 —— public 只剩两个入口页。
    expect(PAGE_ROUTES.filter((row) => row.public).map((row) => row.path)).toEqual([
      '/login',
      '/register',
    ])
  })

  it('清单里没有的路径当场报错，不静默给一份默认 meta', () => {
    expect(pageRow('/nope')).toBe(null)
    expect(() => pageMeta('/nope')).toThrow(/pageRoutes\.json/)
  })
})

describe('vue-router 从清单派生', () => {
  it('注册的每一条页面路径都在清单里，清单里每一页也都注册了', async () => {
    const router = await freshRouter()
    const registered = [...new Set(router.getRoutes().map((record) => record.path))].sort()

    const unknown = registered.filter((path) => !pageRow(path))
    expect(unknown, `注册了清单里没有的路径：${unknown}`).toEqual([])

    const missing = PAGE_ROUTES.filter((row) => !registered.includes(row.path)).map(
      (row) => row.path,
    )
    expect(missing, `清单里的页面没注册（点进去是白屏）：${missing}`).toEqual([])
  })

  it('每条路由的「独立外壳 / 免登录 / 允许的身份」等于清单里的值', async () => {
    const router = await freshRouter()

    for (const row of PAGE_ROUTES) {
      const meta = router.resolve(row.path).meta
      expect(
        { standalone: !!meta.standalone, public: !!meta.public, audience: meta.audience },
        row.path,
      ).toEqual({ standalone: row.standalone, public: row.public, audience: row.audience })
    }
  })

  it('页面标题也从清单来（员工注册页那条）', async () => {
    const router = await freshRouter()

    expect(pageTitle('/register')).toBe('员工注册')
    expect(router.resolve('/register').meta.staffPageTitle).toBe('员工注册')
  })
})

describe('401 兜底豁免也从清单派生（票 10）', () => {
  // 这份名单以前手写在 `utils/loginNext.js`（形如 `/login`、`/settings`、`/staff*`…），
  // 路径一搬家就漂。现在判据只有一条：**这一页的凭据不是管理端会话**就不该被管理端
  // client 甩去 `/login` —— `public` 的页、`audience !== 'admin'` 的页，以及表里显式
  // 标了 `authRedirect: false` 的页（它自己把 401 渲染成「当前未登录」）。
  it('免登录的入口页豁免（登录页自己要用 401 渲染错误）', () => {
    expect(skipsAdminLoginRedirect('/login')).toBe(true)
    expect(skipsAdminLoginRedirect('/register')).toBe(true)
  })

  it('配置页自己处理 401（清单里 authRedirect: false）', () => {
    expect(skipsAdminLoginRedirect('/settings')).toBe(true)
  })

  it('员工身份与「两者皆可」的页豁免（钥匙可能是员工 cookie）', () => {
    expect(skipsAdminLoginRedirect('/workbench/me/today')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/me/month')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/me/clean')).toBe(true)
    // 扫码看配方那条链路：厨师用员工会话读，不该被甩去管理端登录页。
    expect(skipsAdminLoginRedirect('/workbench/kitchen/recipe/detail')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/kitchen/recipe')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/kitchen/prep-plan')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/forbidden')).toBe(true)
  })

  it('管理端专属的页照样跳登录（会话没了就该去登录）', () => {
    for (const path of [
      '/',
      '/admin',
      '/sales-report',
      '/logs',
      '/wecom-push',
      '/workbench/hr/roster',
      '/workbench/floor/daily',
      '/workbench/kitchen/recipe/manage',
      // 票 04：印码页也是管理端那一档（印码是店长布置岗位码的动作）—— 会话没了就该去登录，
      // 不再像「扫码即看的阅读面」那样被豁免。
      '/workbench/kitchen/recipe/qr',
    ]) {
      expect(skipsAdminLoginRedirect(path), path).toBe(false)
    }
  })

  it('尾斜杠与子路径按同一页算（浏览器地址栏里多一个斜杠不该换待遇）', () => {
    expect(skipsAdminLoginRedirect('/workbench/me/today/')).toBe(true)
    expect(skipsAdminLoginRedirect('/admin/')).toBe(false)
  })

  it('清单里没有的（已作废的）路径没有豁免', () => {
    for (const stale of ['/setup', '/staff/today', '/recipe', '/recipe/detail', '/hygiene']) {
      expect(skipsAdminLoginRedirect(stale), stale).toBe(false)
    }
  })
})
