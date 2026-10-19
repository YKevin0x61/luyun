// @vitest-environment jsdom
/**
 * 票 06 / ADR 0106 决策 7：`hygiene-admin.css` 全站只挂一份，判据与 `<html data-workbench>`
 * **同源**（`meta.workbenchTheme`，由页面清单派生）。
 *
 * 断言的是**行为**：把真实路由推到某条地址上，然后数 `document.head` 里指向
 * `/hygiene-admin.css` 的 `<link>` 有几条、`<html data-workbench>` 在不在 ——
 * 改一页的判据（清单里的 `workbenchTheme` / `/workbench` 前缀）条数就跟着变，
 * 而不是「源码里有没有那一行挂载代码」。
 *
 * 改动前的实测（`.scratch/workbench-content-area/baseline/checks-06/before/`）：
 * `/workbench/staff/today` 与 `/workbench/staff/clean` 同页 **2 条** 指向同一文件的 `<link>`
 * （壳 `WorkbenchShell.vue` 一份 + 页面自己 `useScopedStylesheet` 一份），
 * `document.styleSheets` 里也是两份、规则数 600（= 2 × 300）。
 *
 * 页面上「不许再出现自挂」是**源码断言**（有意为之，ADR 0106 Consequences：这类「不许出现 X」
 * 型的护栏与「用源码文本去断行为」是两回事）—— 行为那一半在上面的路由用例里。
 */
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve as resolvePath } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PAGE_ROUTES, pageMeta } from '../router/pageRoutes.js'
import { WORKBENCH_STYLESHEET_HREF } from '../utils/workbenchDocument.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = resolvePath(HERE, '..')
const APP = resolvePath(SRC, '..')

/** 浏览器核对（`check_stylesheet_06.py`）用的是同一个选择器。 */
function stylesheetLinks() {
  return [...document.querySelectorAll('link[href*="hygiene-admin.css"]')]
}

/** 每个用例都换一份崭新的 router 模块：`authStatus` 的 10 秒缓存与 router 单例都不跨用例；
 *  `workbenchDocument` 一起重取，免得断言拿的是旧模块实例。 */
async function freshRouter() {
  vi.resetModules()
  const [routerMod, docMod] = await Promise.all([
    import('../router/index.js'),
    import('../utils/workbenchDocument.js'),
  ])
  return { router: routerMod.default, doc: docMod }
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 两套会话都在（店里那台共用电脑）：守卫怎么走都不会把人踢去登录页，路由落点即断言目标。*/
function bothSessions() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: true, initialized: true })
    if (path.includes('/api/hygiene/staff/session')) {
      return jsonResponse({ authenticated: true, employee: { id: 1, name: '张三' } })
    }
    return jsonResponse({}, 404)
  })
}

function vueFiles(dir) {
  return readdirSync(dir, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.vue'))
    .map((entry) => resolvePath(entry.parentPath || entry.path, entry.name))
}

beforeEach(() => {
  for (const link of stylesheetLinks()) link.remove()
  document.documentElement.removeAttribute('data-workbench')
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('工作台文档级契约：一份样式表 + 一个属性（票 06）', () => {
  it('清单里每一页：条数与 `<html data-workbench>` 同进同退（判据只有清单一处）', async () => {
    const { router, doc } = await freshRouter()
    const themed = []

    for (const row of PAGE_ROUTES) {
      doc.applyWorkbenchDocument(router.resolve(row.path))
      const expected = pageMeta(row.path).workbenchTheme
      expect(stylesheetLinks().length, `${row.path} 的 <link> 条数`).toBe(expected ? 1 : 0)
      expect(
        document.documentElement.hasAttribute('data-workbench'),
        `${row.path} 的 <html data-workbench>`,
      ).toBe(expected)
      if (expected) themed.push(row.path)
    }

    // 防空转：工作台那一档真的有二十多页在跑上面的循环。
    expect(themed.length).toBeGreaterThan(20)
    // 票面点名的四页：三页在工作台里，`/register` 住在顶层路径、靠清单里显式那一行。
    for (const path of [
      '/workbench',
      '/workbench/staff/today',
      '/workbench/floor/daily',
      '/workbench/forbidden',
      '/register',
    ]) {
      expect(themed, `${path} 该有这一份样式表`).toContain(path)
    }
    // 非工作台页一份都不许有（`.btn` / `.card` 那套类名不常驻全局主题）。
    for (const path of ['/', '/admin', '/settings', '/login', '/logs', '/sales-report']) {
      expect(themed, `${path} 不该有这一份样式表`).not.toContain(path)
    }
  })

  // 这一条与上面那条「清单里每一页」一起，是票面验收 1–3 的行为断言：**改路由 → 条数跟着变**。
  // 显式超时（30s）：这六次是**真导航**，每次都要把那一页的懒加载 chunk 读进来
  // （`SetupView` 一个就 160kB），机器被别的活占着时默认那 5 秒不够 —— 与
  // `pageAudienceGate.test.js` 里那个大循环同一类问题。断言本身一个字没放松。
  it('真的导航：路由一变条数跟着变，工作台页之间那一份原地留着', async () => {
    vi.stubGlobal('fetch', bothSessions())
    const { router } = await freshRouter()

    // 改动前这一页挂两份（壳 + 页面），票面验收 1 点名的就是它。
    await router.push('/workbench/staff/today')
    expect(router.currentRoute.value.path).toBe('/workbench/staff/today')
    expect(stylesheetLinks().length).toBe(1)
    const mounted = stylesheetLinks()[0]

    // 工作台页 → 工作台页：不摘了再挂（摘挂之间那一帧正是「首屏无样式跳一下」）。
    await router.push('/workbench/staff/month')
    expect(stylesheetLinks().length).toBe(1)
    expect(stylesheetLinks()[0]).toBe(mounted)

    // 非工作台页：一条都不留；属性一起撤（两者同源，不许一个开一个关）。
    await router.push('/settings')
    expect(stylesheetLinks().length).toBe(0)
    expect(document.documentElement.hasAttribute('data-workbench')).toBe(false)

    // 员工鉴权壳住在顶层路径（`/register`）：靠清单里显式标的那一行把样式表带回来。
    await router.push('/register')
    expect(stylesheetLinks().length).toBe(1)
    expect(document.documentElement.hasAttribute('data-workbench')).toBe(true)

    // 壳外的越权落点也在覆盖范围里（最后一个非工作台页 `/admin` 不在这里再推一次：
    // 上面「清单里每一页」那条已经逐页断言过它的条数是 0、属性也关着）。
    await router.push('/workbench/forbidden')
    expect(stylesheetLinks().length).toBe(1)
    expect(document.documentElement.hasAttribute('data-workbench')).toBe(true)
  }, 30_000)

  it('同一页重复应用不会挂出第二份（composable 自己不做 DOM 去重，这一层必须做）', async () => {
    const { router, doc } = await freshRouter()
    const route = router.resolve('/workbench/staff/today')

    doc.applyWorkbenchDocument(route)
    const mounted = stylesheetLinks()[0]
    expect(mounted).toBeTruthy()

    doc.applyWorkbenchDocument(route)
    doc.applyWorkbenchDocument(router.resolve('/workbench/staff/clean'))
    expect(stylesheetLinks().length).toBe(1)
    expect(stylesheetLinks()[0]).toBe(mounted)
    expect(mounted.getAttribute('href')).toBe(WORKBENCH_STYLESHEET_HREF)
  })
})

describe('挂载收在一处（票 06 的源码护栏）', () => {
  it('没有页面再自己挂这份样式表', () => {
    const offenders = vueFiles(resolvePath(SRC, 'views')).filter((file) =>
      /useScopedStylesheet\(\s*['"]\/hygiene-admin\.css['"]\s*\)/.test(readFileSync(file, 'utf8')),
    )
    expect(
      offenders.map((file) => file.slice(APP.length + 1)),
      '这些页面还在自己挂 hygiene-admin.css（该由文档层那一处管）',
    ).toEqual([])
  })

  it('防跑偏：recipe.css 那 5 处同形挂载没被顺手删掉（本票的非目标）', () => {
    const mounts = vueFiles(resolvePath(SRC, 'views')).filter((file) =>
      /useScopedStylesheet\(\s*['"]\/recipe\.css['"]\s*\)/.test(readFileSync(file, 'utf8')),
    )
    expect(mounts.length).toBe(5)
  })

  it('文档级那一处只有一份实现，且由路由安装（属性与样式表同源）', () => {
    const routerSource = readFileSync(resolvePath(SRC, 'router/index.js'), 'utf8')
    expect(routerSource).toMatch(/installWorkbenchDocument\(router\)/)

    // 全 `src/`（除本文件与那一处实现）不许有人再自己设这个属性 —— 两份判据就是漂移的开始。
    const setters = []
    for (const file of [...vueFiles(SRC), ...jsFiles(SRC)]) {
      if (file === resolvePath(SRC, 'utils/workbenchDocument.js')) continue
      if (file.startsWith(resolvePath(SRC, '__tests__'))) continue
      if (/(set|remove)Attribute\(\s*['"]data-workbench['"]/.test(readFileSync(file, 'utf8'))) {
        setters.push(file.slice(APP.length + 1))
      }
    }
    expect(setters, '这些文件各写了一份 data-workbench 判据').toEqual([])
  })
})

function jsFiles(dir) {
  return readdirSync(dir, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.js'))
    .map((entry) => join(entry.parentPath || entry.path, entry.name))
}
