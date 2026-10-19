/** 页面清单（`pageRoutes.json`）的读取口 —— 加一页先改那张表，再在这里/路由里消费。
 *
 *  表是**唯一来源**：`path` / `title` / `group` / `audience` / `standalone` / `public`
 *  六个必填字段 + 可选字段（`aliases` / `authRedirect` / `workbenchTheme` /
 *  `ownBottomBar` / `flush`，含义见 JSON 里的 `_comment`）。前端路由注册的 meta
 *  （是否独立外壳 / 是否免登录 / 内容区那两个 opt-out）与页面标题都从它派生；后端
 *  `main.py` 的页面路由清单与两张页面豁免表由 `tests/test_spa_page_routes.py` 对着
 *  同一张表强校验。两边一起动的时候，只要表是对的，前端与后端就不会各说各话
 *  （这正是票 01 要消掉的那类漂移）。
 *
 *  身份（`audience`）由路由守卫消费（票 02 起）：`admin` 只认管理端会话、`staff` 只认
 *  员工会话、`both` 任一会话有效即可 —— 与 `main.py` 的页面墙对工作台前缀的判定同一口径。
 */
import table from './pageRoutes.json'

export const PAGE_ROUTES = table.pages

const BY_PATH = new Map(PAGE_ROUTES.map((row) => [row.path, row]))

/** 按路径取表里那一行；不是页面时返回 null。 */
export function pageRow(path) {
  return BY_PATH.get(String(path == null ? '' : path)) || null
}

function requireRow(path) {
  const row = pageRow(path)
  if (!row) {
    // 不兜底、不给默认值：路由不在表里就是清单漏了一页，静默放过等于让它继续漂。
    throw new Error(
      `页面清单里没有 ${path}：加一页要先改 admin-web/src/router/pageRoutes.json`,
    )
  }
  return row
}

/** 页面标题（导航与 document.title 用的名字）。 */
export function pageTitle(path) {
  return requireRow(path).title
}

/** 这一页要不要开工作台令牌层（`<html data-workbench>`）：`/workbench` 前缀下的页
 *  一律开，前缀之外只有清单里显式标了 `workbenchTheme: true` 的页才开 —— 目前只有
 *  `/register`：它套的是工作台那套壳（`HygieneStaffAuthLayout` 的 `.hygiene-staff`）
 *  却住在顶层路径，只按前缀判就整页吃不到 `--hy-*`（输入框无边框无底色、主按钮退化成
 *  一行裸文字）。**判据只有这一处**，`main.js` 不再自己写一份路径前缀。
 *
 *  护栏：`__tests__/pageRoutes.test.js` 里那条「凡渲染工作台壳的页面都开着令牌层」。 */
function workbenchThemeOf(row) {
  return row.workbenchTheme === true || String(row.path).startsWith('/workbench')
}

/** 这一页**自带底部工具栏**（`ownBottomBar`，ADR 0106 决策 5）：手机档不叠工作台底栏、
 *  内容区那份底部让位也还回去。
 *
 *  这份路径名单原先手写在壳里（`WorkbenchShell.vue` 的 `OWN_BOTTOM_BAR_PATHS`），票 02 起
 *  搬进清单 —— 「这一页是什么」只有清单一处来源，壳不再认路径。
 *  **与「铺满」互不隐含**：底下那条栏是谁的不是一回事，内边距还留不留是另一回事；今天
 *  自带底栏的那一页**没有**标铺满，标了铺满的打印页也**没有**自带底栏。
 *
 *  护栏：`views/workbench/__tests__/workbenchShell.test.js` 里那组「opt-out 两个字段从
 *  页面清单派生」（清单点名哪页自带底栏，那一页就不渲染工作台底栏、算出来的下 inset 是 0
 *  —— 票 03 起那份让位由壳算，见 `WorkbenchShell.vue` 的 `contentInsetStyle`，不再是
 *  `<main>` 上的钩子 class）。
 *
 *  **壳读的也是这一份**：`pageMeta` 在路由注册时把它填进 `meta`，壳读
 *  `route.meta.ownBottomBar` —— 壳不再从清单那一行自己判一次（`!!` 与 `=== true` 是两个
 *  口径，清单里出现 `1` / `"true"` 这类真值时结论相反；code-review 收尾修正）。 */
function ownBottomBarOf(row) {
  return row.ownBottomBar === true
}

/** 这一页**铺满**（`flush`，ADR 0106 决策 5）：内容区不要壳给的横向与上内边距 —— A4 预览
 *  （配方打印）与全幅画布那一类页面。
 *
 *  **与「自带底栏」互不隐含**：这两件事只是恰好都曾被壳里那一份 `has-own-bar` 名单表达，
 *  拆开之后各标各的（配方阅读面自带底栏但不铺满，配方打印铺满但不自带底栏）。
 *  壳消费的是 `pageMeta` 归一化后填进路由 `meta` 的那一份（`route.meta.flush`，票 04 起）
 *  —— 与 `ownBottomBar` 同一条读取路径，这里仍只负责归一化。
 *
 *  护栏：`__tests__/pageRoutes.test.js` 的「两个字段互不隐含」（今天真实使用它们的两页
 *  各标各的）。 */
function flushOf(row) {
  return row.flush === true
}

/** 这一页对**员工身份**额外要求的那一项管理能力（`staffCap`，2026-10-14）。
 *
 *  audience 是三态（`admin` / `staff` / `both`），表达不了「店长专属页，但勾了某项能力的
 *  员工也能进」—— 排班三页与损耗分析就是这一档。于是给它一个可选的第四维：
 *  audience 仍是 `admin`（管理端会话照旧直接放行），员工身份还要在花名册上勾了这一项。
 *
 *  **认不出的值原样给出去**：判据是 `hasCap(caps, staffCap)`，不在能力表里的键一律判否
 *  （fail-closed）。这里不校验键名 —— 校验在本文件之外没有任何好处，而清单里的键写错时，
 *  症状会是「员工进不去」而不是「员工乱进」，安全方向是错的。
 *  没有这一项时给 null（守卫与导航都按「不额外要求」处理）。 */
function staffCapOf(row) {
  const value = row.staffCap
  return typeof value === 'string' && value ? value : null
}

/** vue-router 的 meta：独立外壳、免登录、守卫要用的「允许的身份」三态、
 *  员工身份额外要求的那一项能力，这一页要不要开工作台令牌层，以及内容区的两个 opt-out。 */
export function pageMeta(path) {
  const row = requireRow(path)
  return {
    standalone: row.standalone,
    public: row.public,
    audience: row.audience,
    staffCap: staffCapOf(row),
    workbenchTheme: workbenchThemeOf(row),
    ownBottomBar: ownBottomBarOf(row),
    flush: flushOf(row),
  }
}

/** 路径 → 清单里那一行；`/workbench/staff/today/` 与它底下的子路径都算这一页。
 *
 *  `pageRow` 是精确匹配（路由注册用），这里服务的是**运行时地址**：浏览器地址栏里
 *  可能多一个尾斜杠，401 兜底的判据不该因为一个斜杠换待遇。取最长的按整段命中的
 *  前缀 —— `/workbench/hr` 不是一页，别把 `/workbench/hr/roster` 认成它。
 */
function runtimeRow(pathname) {
  const raw = String(pathname == null ? '' : pathname)
  if (!raw) return null
  const trimmed = raw.endsWith('/') && raw !== '/' ? raw.slice(0, -1) : raw
  const exact = BY_PATH.get(trimmed)
  if (exact) return exact
  let best = null
  for (const row of PAGE_ROUTES) {
    if (trimmed.startsWith(`${row.path}/`) && (!best || row.path.length > best.path.length)) {
      best = row
    }
  }
  return best
}

/** 管理端 client 拿到 401 时要不要跳登录页（票 10：这份名单从清单派生，不再手写一份）。
 *
 *  判据只有一条：**这一页的凭据不是管理端会话**，就不该被管理端那套 401 兜底甩去
 *  `/login`：
 *  - `public`（`/login`、`/register`）：本来就不需要会话，登录页自己拿 401 渲染错误；
 *  - `audience !== 'admin'`（`staff` / `both`：我的三页、配方阅读面、工作台首页、
 *    备货计划、无权访问页）：钥匙可能是员工 cookie，甩去管理端登录页是走错门；
 *  - `staffCap`（2026-10-14：排班三页与损耗分析）：这一页也接受**员工会话 + 那项能力**，
 *    员工会话过期时该回员工登录那一栏，不是管理端登录页；
 *  - 表里显式标了 `authRedirect: false` 的页（`/settings`）：它自己把 401 渲染成
 *    「当前未登录」，跳转会和它自己的状态机打架。
 *
 *  系统管理面（`/`、`/admin`、销售报表、企微推送、日志）与工作台里店长专属的那些页
 *  照旧跳登录：它们的会话没了就该去登录。清单里没有的路径（已作废的老地址）也返回
 *  false —— 没有豁免可言。
 */
export function skipsAdminLoginRedirect(pathname) {
  const row = runtimeRow(pathname)
  if (!row) return false
  return (
    row.public === true
    || row.audience !== 'admin'
    || Boolean(row.staffCap)
    || row.authRedirect === false
  )
}
