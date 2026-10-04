// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { PAGE_ROUTES, pageMeta, pageRow, pageTitle } from '../pageRoutes.js'

// 票 01：页面清单（`router/pageRoutes.json`）是唯一来源。这里钉两件事：
// 1. 表本身合法（六个字段齐全、取值在枚举内、路径唯一）；
// 2. 真正注册的 vue-router 路由确实**从表派生** —— 每一条注册路径都在表里、
//    表里每一页都注册了，且每条路由的「独立外壳 / 免登录」标记等于表里的值。
//
// 断言的是行为（注册出来的路由表长什么样），不是源码写法：router 怎么改结构都行，
// 只要还是从这一张表派生。

const GROUPS = ['home', 'hr', 'floor', 'kitchen', 'me', 'system', 'entry']
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
      '/workbench/inbox',
      '/workbench/shifts',
      '/workbench/roster',
      '/workbench/zones',
      '/workbench/daily',
      '/workbench/attire',
      '/workbench/deep-clean',
      '/workbench/fix',
      '/workbench/boards',
      '/workbench/data',
      '/recipe',
      '/recipe/detail',
      '/recipe/print',
      '/recipe/qr',
      // 员工三页搬进工作台的「我的」组（票 03）：套工作台外壳，后台导航照样不渲染；
      // 越权落点也是独立一页（一页说明 + 一颗按钮，不套导航）。
      '/workbench/me/today',
      '/workbench/me/month',
      '/workbench/me/clean',
      '/workbench/forbidden',
    ])
  })

  it('免登录的页面就是这些（登录页、注册页、配方阅读面）', () => {
    // 票 03 起工作台里的页一律不是 public（工作台是「进去要登录」的页面区）：员工三页
    // 从这份名单里退出，页面壳的放行改由「任一会话有效」决定。
    expect(PAGE_ROUTES.filter((row) => row.public).map((row) => row.path)).toEqual([
      '/login',
      '/register',
      '/recipe',
      '/recipe/detail',
      '/recipe/print',
      '/recipe/qr',
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
