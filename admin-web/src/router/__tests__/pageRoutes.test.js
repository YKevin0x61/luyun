// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, resolve as resolvePath } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'
import {
  PAGE_ROUTES,
  pageMeta,
  pageRow,
  pageTitle,
  skipsAdminLoginRedirect,
} from '../pageRoutes.js'

const ROUTER_DIR = resolvePath(dirname(fileURLToPath(import.meta.url)), '..')
/** 应用根（`admin-web/`）：Vitest 把 `import('../views/x.vue')` 改写成
 *  `__vite_ssr_dynamic_import__("/src/views/x.vue")` —— 那种形式是**项目根相对**的，
 *  跟源码里写的相对路径不是一回事，两种都要认。 */
const APP_ROOT = resolvePath(ROUTER_DIR, '../..')

/** 组件（懒加载函数）→ 它的 .vue 文件绝对路径；拿不到就 null，不猜。 */
function componentFile(loader) {
  if (!loader) return null
  const hit = String(loader).match(/['"]([^'"]+\.vue)['"]/)
  if (!hit) return null
  const spec = hit[1]
  return spec.startsWith('/')
    ? resolvePath(APP_ROOT, `.${spec}`)
    : resolvePath(ROUTER_DIR, spec)
}

/** 组件的模板根节点带了工作台那两个 class 之一，或者挂了共享样式表
 *  `hygiene-admin.css` —— 两种都意味着这一页整页在吃 `--hy-*` 令牌。
 *
 *  样式表要认**挂载调用**而不是文件名字符串：`/login` 的注释里就写着「不挂
 *  `/hygiene-admin.css`」（它自带 scoped 配色、明确不依赖那一套），按字面匹配会把它
 *  误判成吃令牌的页。 */
const WORKBENCH_STYLE_RE =
  /class="hygiene-(admin|staff)[ "]|useScopedStylesheet\(\s*['"]\/hygiene-admin\.css['"]\s*\)/

/** 组件文件（含它 import 进来的子组件，往下 `depth` 层）里有没有工作台那套样式？
 *
 *  必须跟着 import 走：现场 8 页与「我的」组的壳 `HygieneAdminLayout` / `WorkbenchLayout`
 *  自己的模板根节点**不带** `.hygiene-admin`，它们是**包着** `WorkbenchShell`（后者才是
 *  带根 class、挂样式表的那一个）。只看 matched 链上文件的字面内容会漏掉这两组。 */
function readsWorkbenchTokens(file, depth = 2) {
  let source
  try {
    source = readFileSync(file, 'utf8')
  } catch {
    return false
  }
  if (WORKBENCH_STYLE_RE.test(source)) return true
  if (depth <= 0) return false
  for (const hit of source.matchAll(/['"](\.[^'"]+\.vue)['"]/g)) {
    if (readsWorkbenchTokens(resolvePath(dirname(file), hit[1]), depth - 1)) return true
  }
  return false
}

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
      // 票 05：工作台按组落位 —— 人事五页（月历 / 待办 / 班次表 / 花名册 / 人事提醒）。
      '/workbench/hr/calendar',
      '/workbench/hr/inbox',
      '/workbench/hr/shifts',
      '/workbench/hr/roster',
      // 人事提醒（overtime-and-reminders 票 05）：工龄奖该调名单 + 档案待补，同套工作台外壳。
      '/workbench/hr/reminders',
      // 加班统计（overtime-and-reminders 票 02）：超管的审批台（待办 / 批驳 / 作废 / 代录），
      // 套的是人事那一组的壳。员工那一面在 `/workbench/me/overtime`（票 01）。
      '/workbench/hr/overtime',
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

describe('工作台令牌层（`html[data-workbench]`）从清单派生', () => {
  // 实测缺陷（2026-10-13 截图复核）：`/register` 套的是工作台那套壳
  // （`HygieneStaffAuthLayout` 的 `.hygiene-staff`），但令牌层当时只按 `/workbench`
  // 路径前缀打开，于是注册页整页**吃不到 `--hy-*`**：输入框的 background/border
  // 整条声明失效（CSS 变量未定义时退化为 initial，**不会**回落到浏览器默认样式）→
  // 无边框无底色；`btn-primary` 的薄荷渐变失效 → 主按钮退化成一行裸文字；三档文字色
  // 塌成一档；`:focus` 的边框与光晕失效而 `outline:none` 还在 → 聚焦零反馈。
  //
  // 判据从此只有一处：清单里的 `workbenchTheme`（由页面清单的读取口算出），
  // main.js 不再自己写一份路径前缀。

  it('注册页与工作台全部页面开着令牌层，其余页面关着', () => {
    for (const row of PAGE_ROUTES) {
      const expected = row.path === '/register' || row.path.startsWith('/workbench')
      expect(pageMeta(row.path).workbenchTheme, row.path).toBe(expected)
    }
  })

  it('注册页在表里显式标了 workbenchTheme（不在工作台前缀下，只能点名）', () => {
    expect(pageRow('/register').workbenchTheme).toBe(true)
  })

  it('凡渲染工作台壳的页面都开着令牌层 —— 漏开就是整页掉色', async () => {
    const router = await freshRouter()
    const shelled = []
    for (const row of PAGE_ROUTES) {
      const matched = router.resolve(row.path).matched
      const usesShell = matched.some((record) =>
        readsWorkbenchTokens(componentFile(record.components?.default)),
      )
      if (usesShell) shelled.push(row.path)
    }
    // 防空转：壳的解析一旦失效（换写法 / 改懒加载形态），先在这条上红，
    // 而不是让下面那个循环空转着绿。
    expect(shelled, '没解析出注册页的工作台壳，检查 componentFile 的路径解析').toContain(
      '/register',
    )
    expect(shelled.length, `解析出的壳页面偏少：${JSON.stringify(shelled)}`).toBeGreaterThan(20)
    for (const path of shelled) {
      expect(pageMeta(path).workbenchTheme, `${path} 套了工作台壳却没开令牌层`).toBe(true)
    }
  })
})
