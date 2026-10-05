// @vitest-environment jsdom
/**
 * 页面清单契约门（`.scratch/admin-caps/issues/05-page-audience-contract-gate.md`）：
 * 清单里的 `audience` 一列与**实际可达性**对表。
 *
 * 做法是**按清单遍历、把页面按身份真的渲染出来**，再看渲染结果里每一条入口指向的页
 * 是不是这个身份打得开的：
 *
 *  - **员工可达面**（`public` / `both` / `staff` 的页，含它们套着的工作台外壳与导航）上，
 *    不许出现 `admin` 专属页的入口 —— 员工点下去只会吃一张「无权访问」；
 *  - **管理端导航面**（后台那条导航 + 店长那一档的工作台导航）上，不许出现 `staff`
 *    专属页的入口。
 *
 * 「打得开」的判据只有两条：公开页（`public: true`）谁都能开；其余看清单里那一行的
 * `audience`。断言的是**渲染出来的 `<a href>`**（含 `router-link` 与壳里的门），不看源码
 * 怎么写 —— 这道门判的是可达性，不是实现方式。
 *
 * 身份不由测试硬塞进 store，而是让**应用自己按会话推**（stub 出来的一套会话 +
 * 本机记忆值），导航与页面入口因此走的是真实那条路。pinia 在 test 模式下会忽略显式传入
 * 的 pinia 实例（`useStore` 一律取 `getActivePinia()`），测试里从外部改 store 不可靠 ——
 * 那些「渲染出来的是什么」的断言会静默变成「什么都没渲染」。
 *
 * **这道门测不到什么**（spec 的 Testing Decisions 第 3 条与 Out of Scope 最后一条明说）：
 *
 *  1. **`audience` 这一列的值对不对，它测不到。**「这一页该给谁」唯一的依据是页面的实际
 *     用途，属人工判断，没有能自动核对的第二来源。它只能保证「清单与导航 / 入口的可达性
 *     一致」这一层：一页其实是店长的却标成了 `both`、而它又确实出现在员工导航里时，
 *     这道门是**绿**的（两者自洽）。**要抓「标错」，只能靠人复核这一列。**
 *  2. **按钮里 `router.push` / `router.replace` 的落点**：判据是渲染出来的链接
 *     （`<a href>`）。写在 `<button>` 上的跳转目标不在里面 —— 那要逐个点下去（还会触发
 *     真实写请求），不适合当门。
 *  3. **服务端页面墙**：那一半的同类断言在 `tests/test_spa_page_routes.py`
 *     （工作台前缀外的管理端页面对员工会话仍 302；清单说员工该进的页服务端必须放行）。
 *     `/workbench/*` 里的管理端页**服务端本就不按 audience 拦**（ADR 0092：页面级权限交给
 *     前端路由 meta 与各接口的 401），所以那一档只有这道门守着。
 *  4. **页面没渲染出来的那部分**：跳转目标写了但被 `v-if` 挡住、或页面自己那次请求失败
 *     之后的形态，都不在判据里 —— 这道门看的是「此刻渲染出来的入口」。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { RouterView } from 'vue-router'
import { createPinia } from 'pinia'

import router from '../index.js'
import { PAGE_ROUTES, pageRow } from '../pageRoutes.js'
import NavBar from '../../components/NavBar.vue'
import { clearAuthStatusCache } from '../../utils/authStatus.js'
import { RECIPE_QR_PATH } from '../../utils/recipePaths.js'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from '../../utils/workbenchIdentity.js'
import { workbenchAudienceFor } from '../../utils/workbenchNav.js'

/** jsdom 缺的那几样浏览器 API：这道门要真的挂载页面，缺一个就在 `onMounted` 里炸。
 *
 *  只在测试环境补齐，不改页面：`matchMedia` / `ResizeObserver` / `IntersectionObserver`
 *  在真实浏览器里都有。 */
function installBrowserShims() {
  vi.stubGlobal('matchMedia', (query) => ({
    matches: false,
    media: String(query),
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  }))
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
  })
  vi.stubGlobal('IntersectionObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return [] }
  })
  window.print = () => {}
  window.scrollTo = () => {}
  window.alert = () => {}
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 员工这一档的会话：管理端会话不在、员工会话有效。
 *
 *  身份因此由应用自己定成员工（本机记忆值是 `staff`，见 `renderRoute`）—— 不走测试塞值。
 *  未知接口一律回空对象（不是 404）：这道门压的是**入口指向哪儿**，不是各页取数成功与否；
 *  让接口层抛错会把页面打回错误态，反倒盖住要看的入口。已知形状的那几个给最小可用数据，
 *  免得页面在渲染期读到 `undefined` 崩掉（那是环境问题，不是要断的行为）。 */
function staffSessionFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    if (path.includes('/api/hygiene/staff/me')) {
      return jsonResponse({ employee: { name: '张三', admin_caps: [] } })
    }
    if (path.includes('/api/scheduling/me')) {
      return jsonResponse({ today: '2026-10-06', days: [], zones: [] })
    }
    if (path.includes('/api/recipes/stations')) return jsonResponse({ stations: [] })
    return jsonResponse({})
  })
}

/** 超级管理员那一档：管理端会话在、员工会话不在（记忆值是 `super`）。 */
function adminSessionFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: true, initialized: true })
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ detail: '需要员工登录' }, 401)
    if (path.includes('/api/scheduling/calendar')) {
      return jsonResponse({ month: '2026-10', days: [], groups: [] })
    }
    if (path.includes('/api/recipes/stations')) return jsonResponse({ stations: [] })
    return jsonResponse({})
  })
}

/** 挂载出来的壳，逐个登记：用例中途失败时也要在 afterEach 里卸掉 ——
 *  一个还挂着的 `RouterView` 会跟着 router 跳到下一个用例的路径上去。 */
const mounted = []

function track(wrapper) {
  mounted.push(wrapper)
  return wrapper
}

/** 渲染一个页面（含它套着的壳）。
 *
 *  用**真 router**（`../index.js`）：入口组件从路由记录里来，不在这里另抄一份
 *  「路径 → 组件」的对照表 —— 清单里加一页、路由跟着注册之后，这条门自己就跟上。 */
async function renderRoute(path, identity) {
  window.localStorage.clear()
  window.localStorage.setItem('luyun.login.workbench.identity', identity)
  clearAuthStatusCache()
  vi.stubGlobal('fetch', identity === IDENTITY_STAFF ? staffSessionFetch() : adminSessionFetch())

  const pinia = createPinia()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(RouterView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  await flushPromises()

  // 守卫没把人改道，这一页确实渲染出来了（改道了就会去看另一页的入口，那是静默的洞）。
  expect(router.currentRoute.value.path, `${path} 被守卫改道了`).toBe(path)
  return track(wrapper)
}

/** 渲染一颗组件（后台那条导航用：它的几格是写死的，不读身份）。 */
async function renderComponent(component, path) {
  window.localStorage.clear()
  clearAuthStatusCache()
  vi.stubGlobal('fetch', adminSessionFetch())

  const pinia = createPinia()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return track(wrapper)
}

/** 渲染结果里所有站内链接的**页面路径**（去掉 query / hash 与尾斜杠）。
 *
 *  站外地址与页内锚点（`#main`）不是「入口」，跳过。 */
function linkedPaths(wrapper) {
  const paths = new Set()
  for (const anchor of wrapper.findAll('a[href]')) {
    const href = String(anchor.attributes('href') || '')
    if (!href.startsWith('/') || href.startsWith('//')) continue
    const raw = href.split(/[?#]/)[0]
    paths.add(raw.length > 1 ? raw.replace(/\/+$/, '') : raw)
  }
  return [...paths].sort()
}

/** 这一页这个身份打不打得开（清单说的）。
 *
 *  - 清单里没有的路径：不是页面（`/api/...`、静态资源），不归这道门管；
 *  - 公开页（`public: true`）：谁都能开 —— `/login` 上的「自助注册」就是一例；
 *  - 其余按 `audience`：`both` 两档都开，`admin` / `staff` 各一边。
 *
 *  身份词（`super` / `staff`）翻成清单里的词（`admin` / `staff`）走
 *  `workbenchAudienceFor` —— 这个映射全仓只有那一处，这里不再抄一份副本（抄的那份一旦
 *  漏改，这道门会拿错的身份词去比对，症状是它静默放行）。 */
function openableBy(path, identity) {
  const row = pageRow(path)
  if (!row) return { judged: false }
  if (row.public) return { judged: true, ok: true, row }
  const ok = row.audience === 'both' || row.audience === workbenchAudienceFor(identity)
  return { judged: true, ok, row }
}

/** 这些入口里，哪些的目标页**这个身份打不开** —— 一条都不许有。 */
function unreachableEntries(wrapper, identity) {
  const offenders = []
  for (const path of linkedPaths(wrapper)) {
    const verdict = openableBy(path, identity)
    if (!verdict.judged || verdict.ok) continue
    offenders.push(`${path}（清单里是 ${verdict.row.audience}：${verdict.row.title}）`)
  }
  return offenders
}

/** 清单里所有「这个身份进得去」的页（公开页也算：谁都能开）。 */
function pagesReachableBy(identity) {
  const audience = workbenchAudienceFor(identity)
  return PAGE_ROUTES.filter(
    (row) => row.public || row.audience === 'both' || row.audience === audience,
  ).map((row) => row.path)
}

/** 工作台那条导航渲染出来的格子（页面里就挂着它；这里从页面上取，不另起一份）。 */
function navItemsOf(wrapper) {
  return wrapper.findAll('.wb-nav-item').map((item) => item.attributes('href'))
}

beforeEach(() => {
  installBrowserShims()
  window.localStorage.clear()
})

afterEach(() => {
  for (const wrapper of mounted.splice(0)) {
    try {
      wrapper.unmount()
    } catch {
      // 用例里已经卸过了。
    }
  }
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('管理端专属页不出现在员工可达的面上（票 05 那道门）', () => {
  it('清单里每个员工进得去的页，员工身份渲染出来都没有指向管理端专属页的入口', async () => {
    // 面从清单派生：`public` / `both` / `staff` 的页都是员工进得去的（现在 11 页）。
    const paths = pagesReachableBy(IDENTITY_STAFF)
    expect(paths.length).toBeGreaterThanOrEqual(8)

    const offenders = []
    let anchorsSeen = 0
    for (const path of paths) {
      const wrapper = await renderRoute(path, IDENTITY_STAFF)
      anchorsSeen += linkedPaths(wrapper).length
      for (const bad of unreachableEntries(wrapper, IDENTITY_STAFF)) {
        offenders.push(`${path} → ${bad}`)
      }
      wrapper.unmount()
    }

    // 「一页都没渲染出入口」会让上面那条断言空过 —— 先证明这一趟真的看到了入口。
    expect(anchorsSeen, '一个站内入口都没渲染出来，这条门的判据是空的').toBeGreaterThan(0)
    expect(offenders, '员工可达的面上出现了管理端专属页的入口').toEqual([])
  })

  it('员工那一档的工作台导航：渲染出来的每一格都指向员工打得开的页', async () => {
    const wrapper = await renderRoute('/workbench', IDENTITY_STAFF)

    // 导航不是空的（身份认不出时它会一格都不渲染，那样下面那条断言会空过）。
    expect(navItemsOf(wrapper)).toEqual([
      '/workbench',
      '/workbench/me/clean',
      '/workbench/kitchen/recipe',
      '/workbench/kitchen/prep-plan',
      '/workbench/me/today',
    ])
    expect(unreachableEntries(wrapper, IDENTITY_STAFF)).toEqual([])
    wrapper.unmount()
  })

  it('配方列表页（`both`）：员工这一档没有印码入口，店长那一档照旧有（票 04 归属的收口）', async () => {
    // 印码页（`/workbench/kitchen/recipe/qr`）票 04 起是 `admin` 专属：员工点它只会落
    // 「无权访问」。那颗按钮 2026-10-06 之前是无条件渲染的 —— 判据与同页那两颗顶栏入口
    // 一样（`useRecipeAdmin` 的工作台身份），这里把两边都钉住。
    const staffWrapper = await renderRoute('/workbench/kitchen/recipe', IDENTITY_STAFF)
    expect(linkedPaths(staffWrapper)).not.toContain(RECIPE_QR_PATH)
    expect(staffWrapper.find(`a[href="${RECIPE_QR_PATH}"]`).exists()).toBe(false)
    staffWrapper.unmount()

    const adminWrapper = await renderRoute('/workbench/kitchen/recipe', IDENTITY_ADMIN)
    expect(adminWrapper.find(`a[href="${RECIPE_QR_PATH}"]`).exists()).toBe(true)
    adminWrapper.unmount()
  })
})

describe('员工专属页不出现在管理端导航里（同一道门的反向）', () => {
  it('后台那条导航渲染出来的每一格，管理员都打得开', async () => {
    const wrapper = await renderComponent(NavBar, '/')

    // 那条导航不是空的（写死的六格）。
    expect(wrapper.findAll('.nav-tab').length).toBeGreaterThan(0)
    expect(unreachableEntries(wrapper, IDENTITY_ADMIN)).toEqual([])
    wrapper.unmount()
  })

  it('店长那一档的工作台导航：渲染出来的每一格，管理员都打得开', async () => {
    const wrapper = await renderRoute('/workbench/hr/calendar', IDENTITY_ADMIN)

    expect(navItemsOf(wrapper)).toEqual([
      '/workbench',
      '/workbench/hr/calendar',
      '/workbench/floor/daily',
      '/workbench/kitchen/recipe',
      '/workbench/kitchen/prep-plan',
    ])
    expect(unreachableEntries(wrapper, IDENTITY_ADMIN)).toEqual([])
    wrapper.unmount()
  })

  it('清单里确实有员工专属页（否则「反向」那两条是空的）', () => {
    const staffOnly = PAGE_ROUTES.filter((row) => row.audience === 'staff').map((row) => row.path)
    expect(staffOnly.length).toBeGreaterThan(0)
    // 员工端那三页都在里面（它们是「员工专属」这一档的实体）。
    expect(staffOnly).toContain('/workbench/me/today')
    expect(staffOnly).toContain('/workbench/me/clean')
  })
})
