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

/** 组件的模板根节点带了工作台那两个 class 之一 —— 意味着这一页整页在吃 `--hy-*` 令牌。
 *
 *  这里**曾经**还认一个证据：`useScopedStylesheet('/hygiene-admin.css')` 那条挂载调用。
 *  票 06 之后那一层收到文档级（`utils/workbenchDocument.js`，路由一处安装），页面文件里
 *  不再有这条调用，所以判据只剩 class —— 「凡渲染工作台壳的页面都开着令牌层」这条口径
 *  一个字没变，只是证据换回了模板根节点上的 class（`.hygiene-admin` / `.hygiene-staff`）。
 *  挂载条数本身的行为断言在 `src/__tests__/workbenchStylesheet.test.js`。 */
const WORKBENCH_STYLE_RE = /class="hygiene-(admin|staff)[ "]/

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

  it('staffCap 只标在这四页上，audience 仍是 admin（它是第四维，不是第二份 audience）', () => {
    // 2026-10-14 用户裁定：排班三页（人事组）与损耗分析（后勤组）对「勾了对应能力的员工」
    // 也开。audience 仍是 admin —— 管理端会话照旧直接放行，员工身份再要那一项能力。
    const withCap = PAGE_ROUTES.filter((row) => row.staffCap)
    expect(withCap.map((row) => [row.path, row.staffCap])).toEqual([
      ['/workbench/hr/calendar', 'schedule'],
      ['/workbench/hr/inbox', 'schedule'],
      ['/workbench/hr/shifts', 'schedule'],
      ['/workbench/kitchen/loss', 'wastage'],
    ])
    for (const row of withCap) expect(row.audience, row.path).toBe('admin')
    // meta 透出同一个值；没有这一项的页给 null（守卫与导航都按「不额外要求」处理）。
    expect(pageMeta('/workbench/hr/calendar').staffCap).toBe('schedule')
    expect(pageMeta('/workbench/kitchen/loss').staffCap).toBe('wastage')
    expect(pageMeta('/workbench/hr/roster').staffCap).toBeNull()
    expect(pageMeta('/workbench/staff/today').staffCap).toBeNull()
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
      // 套的是人事那一组的壳。员工那一面在 `/workbench/staff/overtime`（票 01）。
      '/workbench/hr/overtime',
      // 考勤表（考勤表导出 票 01）：人事组的第七页 —— 当月一人一张卡，同套人事壳。
      '/workbench/hr/attendance',
      // 节假日日历（考勤表导出 票 02）：超管逐日标「这天是法定节假日」，套人事那一组的壳。
      '/workbench/hr/holidays',
      // 现场页（卫生）。
      '/workbench/floor/zones',
      // 日常清单（2026-10-19）：手机的独立落点（桌面端仍是工作区页右栏），套同一套现场壳。
      '/workbench/floor/zone-items',
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
      '/workbench/kitchen/prep-review',
      // 备货品换算规则（2026-10-26）：从销售报表的 tab 搬进后勤组，同套工作台外壳。
      '/workbench/kitchen/prep-rules',
      '/workbench/kitchen/prep-items',
      // 损耗分析（从 worktool2 那个小程序搬过来）：后勤组的第三页，同套工作台外壳。
      '/workbench/kitchen/loss',
      // 员工三页搬进工作台的「我的」组（票 03）：套工作台外壳，后台导航照样不渲染；
      // 越权落点也是独立一页（一页说明 + 一颗按钮，不套导航）。
      '/workbench/staff/today',
      '/workbench/staff/month',
      // 加班与补钟（票 01）：员工自己提一笔、看自己的记录与月度净时长，同套工作台外壳。
      '/workbench/staff/overtime',
      '/workbench/staff/clean',
      // 损耗登记（从 worktool2 那个小程序搬过来）：「我的」组里那一页 —— 记一笔 + 看自己
      // 最近记的。`audience: both`（店长也登记），外壳与同组那几页一样。
      '/workbench/staff/loss',
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
    expect(skipsAdminLoginRedirect('/workbench/staff/today')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/staff/month')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/staff/clean')).toBe(true)
    // 扫码看配方那条链路：厨师用员工会话读，不该被甩去管理端登录页。
    expect(skipsAdminLoginRedirect('/workbench/kitchen/recipe/detail')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/kitchen/recipe')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/kitchen/prep-plan')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/forbidden')).toBe(true)
  })

  it('带 staffCap 的店长页也豁免（钥匙可能是员工会话 + 那项能力）', () => {
    // 2026-10-14：排班三页与损耗分析接受「员工会话 + 勾了那项能力」，所以员工会话过期时
    // 该回员工登录那一栏，不该被管理端 401 兜底甩去 `/login`。
    expect(skipsAdminLoginRedirect('/workbench/hr/calendar')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/hr/inbox')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/hr/shifts')).toBe(true)
    expect(skipsAdminLoginRedirect('/workbench/kitchen/loss')).toBe(true)
    // 花名册没有 staffCap（身份证明细与底薪不下发员工端）：仍按管理端那一档跳登录。
    expect(skipsAdminLoginRedirect('/workbench/hr/roster')).toBe(false)
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
    expect(skipsAdminLoginRedirect('/workbench/staff/today/')).toBe(true)
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

describe('内容区 opt-out 也从清单派生（票 02 / ADR 0106 决策 5）', () => {
  // opt-out 原来是壳里的一份手写路径名单（`WorkbenchShell.vue` 的 `OWN_BOTTOM_BAR_PATHS`）。
  // 两个可选字段接过来之后，判据只有清单这一处，壳只读它渲染。
  const OPT_OUT_FIELDS = ['ownBottomBar', 'flush']

  it('两个字段都只接受 true（缺省即不生效），不写第二份默认值', () => {
    for (const row of PAGE_ROUTES) {
      for (const field of OPT_OUT_FIELDS) {
        if (field in row) {
          expect(row[field], `${row.path} 的 ${field} 只认 true`).toBe(true)
        }
      }
    }
  })

  it('两个字段互不隐含：今天真实用着它们的两页各标各的', () => {
    // 配方阅读面：自带底栏（页内那条 `.sop-bottom-bar`），不铺满。
    expect(pageRow('/workbench/kitchen/recipe/detail').ownBottomBar).toBe(true)
    expect(pageRow('/workbench/kitchen/recipe/detail').flush).toBeUndefined()
    // 配方打印：A4 预览要铺满，但它底下没有自带底栏 —— 这正是两个字段拆开的意义
    // （旧名单里 `has-own-bar` 同时做了这两件事）。
    expect(pageRow('/workbench/kitchen/recipe/print').flush).toBe(true)
    expect(pageRow('/workbench/kitchen/recipe/print').ownBottomBar).toBeUndefined()
  })

  it('壳那侧读的就是这两个字段（缺省即不生效）', () => {
    expect(pageMeta('/workbench/kitchen/recipe/detail').ownBottomBar).toBe(true)
    expect(pageMeta('/workbench/kitchen/recipe/detail').flush).toBe(false)
    expect(pageMeta('/workbench/kitchen/recipe/print').flush).toBe(true)
    expect(pageMeta('/workbench/kitchen/recipe/print').ownBottomBar).toBe(false)
    // 其余页面一个字没变：两项都关着。
    for (const path of ['/workbench', '/workbench/staff/today', '/workbench/floor/daily', '/login']) {
      expect(pageMeta(path).ownBottomBar, path).toBe(false)
      expect(pageMeta(path).flush, path).toBe(false)
    }
  })

  it('清单里那行 `_comment` 写明了字段名 / 默认值 / 判据在哪 / 护栏在哪', () => {
    // 可选字段的先例（`authRedirect` / `workbenchTheme`）：说明行里这四件事缺一不可 ——
    // 只写「有这个字段」，下一个维护者就得回源码里反推默认值与护栏。
    const comment = readFileSync(resolvePath(ROUTER_DIR, 'pageRoutes.json'), 'utf8')
    for (const field of OPT_OUT_FIELDS) {
      const line = comment.split('\n').find((text) => text.includes(`可选字段 ${field}`))
      expect(line, `pageRoutes.json 的 _comment 里没有「可选字段 ${field}」那一条`).toBeTruthy()
      expect(line, `${field} 那条没写默认值`).toContain('默认')
      expect(line, `${field} 那条没写判据在哪`).toContain('判据在 admin-web/src/router/pageRoutes.js')
      expect(line, `${field} 那条没写护栏在哪`).toContain('护栏：')
      // 护栏指向的用例必须真的存在（说明行指错文件就是又一次漂移）。
      expect(line).toContain('workbenchShell.test.js')
    }
  })

  it('两个字段都只给套工作台壳的页面用（壳外的页面没有这份 opt-out）', () => {
    const users = PAGE_ROUTES.filter((row) =>
      OPT_OUT_FIELDS.some((field) => row[field] === true),
    ).map((row) => row.path)
    for (const path of users) {
      expect(path.startsWith('/workbench'), `${path} 不在工作台里，壳管不到它`).toBe(true)
    }
    // 防空转：今天至少有一页在用（`ownBottomBar` 与 `flush` 各一页）。
    expect(users).toContain('/workbench/kitchen/recipe/detail')
    expect(users).toContain('/workbench/kitchen/recipe/print')
  })
})
