/** Build and resolve /login?next= without double-encoding query values.
 *
 *  配方阅读面那四条路径不在这里写第二遍：唯一一份在 `utils/recipePaths.js`
 *  （页面里的 router-link、印码页生成的地址、这里的回跳白名单共用它）。
 *  免墙名单（`main.py` 的 `HTML_AUTH_PUBLIC_PAGES`）票 07 起已经是空的 ——
 *  配方阅读面也要登录，这一份不再是「免墙名单」而是「扫码回跳白名单」。
 *
 *  **401 兜底的豁免名单不在这里**（票 10）：它从页面清单派生，判据是
 *  `router/pageRoutes.js` 的 `skipsAdminLoginRedirect` —— 这里只回答「登录之后回哪儿」，
 *  不再兼管「哪一页不该被甩去登录」（那份名单以前手写在这里，路径一搬家就漂）。
 */
import { RECIPE_READER_PATHS, isRecipeReaderPath } from './recipePaths.js'
import { pageRow } from '../router/pageRoutes.js'
import { STAFF_ENTRY_PATH, isStaffLandingPath } from './staffPaths.js'

export { RECIPE_READER_PATHS, isRecipeReaderPath }

export function buildLoginNextFromRoute(route) {
  const path = (route && route.path) || '/'
  const query = (route && route.query) || {}
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value == null || value === '') continue
    const values = Array.isArray(value) ? value : [value]
    for (const item of values) {
      if (item == null || item === '') continue
      params.append(key, String(item))
    }
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

/** 登录页的路径（票 12：落点与判据都从这一份取，别在两处判据里各写一遍字符串）。 */
export const LOGIN_PATH = '/login'

/** 当前是不是已经站在登录页上（带不带 `?next=` 都算）。 */
export function isOnLoginPage(path) {
  return String(path == null ? '' : path) === LOGIN_PATH
}

/** 「会话没了，回登录页」这个动作的落点：`/login` + 原目标（`?next=`）。
 *
 *  **已经在登录页上时返回 `null`**（不跳）。这不是优化，是止损：一页上并发的几个请求会
 *  各拿一个 401，第一个把人送到 `/login?next=X`，后面几个若再跳一次，就会把「已经是登录
 *  页的当前地址」当成原目标包进去 —— `/login?next=/login?next=X` 那种套娃，`?next=` 里
 *  真正要回去的目标当场作废（票 12 收的 O2，真机实测过）。
 *
 *  组件侧（员工三页）与 `main.js` 注入给 `api/client.js` 的 401 兜底共用这一份判据，
 *  不各写一遍 —— 写两遍就会一处修、一处漏。
 */
export function loginRedirectTarget(route) {
  const current = route || {}
  if (isOnLoginPage(current.path)) return null
  return { path: LOGIN_PATH, query: { next: buildLoginNextFromRoute(current) } }
}

/** 站内相对路径的公共判据：站外、协议相对、反斜杠变体、登录页自身一律不算。
 *
 *  按身份互斥的两份白名单（管理员 / 员工）都从这里出发 —— 不各写一遍正则，否则
 *  两份会漂：强的那份放松了也没有断言拦得住。
 */
function sanitizeNext(raw) {
  if (raw == null || raw === '') return null
  let next = Array.isArray(raw) ? raw[0] : String(raw)
  if (next == null || next === '') return null
  if (/%25/i.test(next)) {
    try {
      next = decodeURIComponent(next)
    } catch {
      return null
    }
  }
  if (!next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return null
  if (next === '/login' || next.startsWith('/login?') || next.startsWith('/login#')) return null
  return migrateLegacyPath(next)
}

/** 只看路径部分：query / hash 不参与前缀判断。 */
function pathnameOf(next) {
  const [pathname] = String(next).split(/[?#]/)
  return pathname
}

/** 老地址 → 工作台地址。**只在 `?next=` 这个入口换，不给老路径留路由。**
 *
 *  `?next=` 里可能存着搬家前的老地址：别人手机上存着的旧链接、旧书签、旧 PWA 快捷
 *  方式，或者上一次登录被挡下来时写进 URL 的那条。老路由已经删干净了，原样放行就是
 *  跳进一个白屏 —— 所以在这里换掉。
 *
 *  **顺序就是优先级，从上到下第一条命中即用**：
 *  - 票 05（工作台按组分家）那批：票 04 的平铺 `/workbench/*`（刚作废的正式地址）
 *    与更早的 `/hygiene/*`、`/scheduling*` 一起换成 `/workbench/hr/*` 或
 *    `/workbench/floor/*`；
 *  - 票 03 的 `/staff/*` → `/workbench/me/*`：`/staff/today` 是**唯一被发出去过**的
 *    员工地址（花名册页那张二维码、登录页的 `?next=`），所以这条最值钱；
 *  - 最后是老的裸前缀兜底（`/hygiene/` → `/workbench/`），挂不上具体某一页的那些。
 *
 *  **匹配按整段**（`from` 或 `from/` 两种写法），不按字符串前缀：`/workbench/daily-x`
 *  不是 `/workbench/daily`，别把不相干的路径也搬走。带尾斜杠的前缀条目（`/staff/`）
 *  照旧按前缀算 —— 那正是"这个前缀底下的一切"。
 *  地址本身照旧 404，这里换的只是「登录之后回哪儿」。
 */
const LEGACY_NEXT_PATHS = [
  // ── 票 08：备货计划从管理后台搬进工作台的「后勤」组 ─────────────────────────
  // 它是**单个地址**（不是前缀底下的一族），所以只有整段匹配这一条；带尾斜杠的写法由
  // `legacyHit` 一并接住（`/prep-plan/` → 新地址 + `/`）。查询串与 hash 原样带过去。
  ['/prep-plan', '/workbench/kitchen/prep-plan'],
  // ── 票 07：配方从独立域搬进工作台的「后勤」组 ─────────────────────────────
  // 岗位码里印的是老地址（`/recipe/detail?slug=…`）时，扫码的人被挡到登录页、`?next=`
  // 就是这一条 —— 不换的话登录之后回到一个 404。五条逐条换（整段匹配），查询串与 hash
  // 原样带过去；`/recipe/` 那条兜底挂在最后，接住前缀底下别的写法。
  ['/recipe/detail', '/workbench/kitchen/recipe/detail'],
  ['/recipe/print', '/workbench/kitchen/recipe/print'],
  ['/recipe/manage', '/workbench/kitchen/recipe/manage'],
  ['/recipe/qr', '/workbench/kitchen/recipe/qr'],
  ['/recipe', '/workbench/kitchen/recipe'],
  // ── 票 05：工作台分成「人事 / 现场」两组，平铺的地址换成新分组 ──────────────
  ['/workbench/inbox', '/workbench/hr/inbox'],
  ['/workbench/shifts', '/workbench/hr/shifts'],
  ['/workbench/roster', '/workbench/hr/roster'],
  ['/workbench/zones', '/workbench/floor/zones'],
  ['/workbench/daily', '/workbench/floor/daily'],
  ['/workbench/attire', '/workbench/floor/attire'],
  ['/workbench/deep-clean', '/workbench/floor/deep-clean'],
  ['/workbench/fix', '/workbench/floor/fix'],
  ['/workbench/boards', '/workbench/floor/boards'],
  ['/workbench/data', '/workbench/floor/data'],
  // ── 票 04 / 05 之前那两套老前缀，直接换成新分组（中间那一版平铺地址从不存在于
  //    它们的 `?next=` 里，所以不必先换成平铺再换一次）────────────────────────
  ['/hygiene/roster', '/workbench/hr/roster'],
  ['/hygiene/zones', '/workbench/floor/zones'],
  ['/hygiene/daily', '/workbench/floor/daily'],
  ['/hygiene/attire', '/workbench/floor/attire'],
  ['/hygiene/deep-clean', '/workbench/floor/deep-clean'],
  ['/hygiene/fix', '/workbench/floor/fix'],
  ['/hygiene/boards', '/workbench/floor/boards'],
  ['/hygiene/data', '/workbench/floor/data'],
  ['/scheduling/inbox', '/workbench/hr/inbox'],
  ['/scheduling/shifts', '/workbench/hr/shifts'],
  ['/scheduling', '/workbench/hr/calendar'],
  // ── 票 03：员工端三页搬进工作台的「我的」组 ─────────────────────────────────
  ['/staff/', '/workbench/me/'],
  // ── 兜底：老前缀底下别的地址（今天已经没有对应的页了，换过去也是 404，但至少还在
  //    工作台里，不会把人送进一个连外壳都没有的空路径）────────────────────────
  ['/hygiene/', '/workbench/'],
  ['/recipe/', '/workbench/kitchen/recipe/'],
]

/** 一条老地址规则命中没有：`from` 带尾斜杠 = 前缀规则，否则整段匹配（含尾斜杠写法）。 */
function legacyHit(pathname, from) {
  if (from.endsWith('/')) {
    return pathname.startsWith(from) ? pathname.slice(from.length) : null
  }
  if (pathname === from) return ''
  if (pathname === `${from}/`) return '/'
  return null
}

function migrateLegacyPath(next) {
  const raw = String(next)
  const cut = raw.search(/[?#]/)
  const pathname = cut === -1 ? raw : raw.slice(0, cut)
  const tail = cut === -1 ? '' : raw.slice(cut)
  for (const [from, to] of LEGACY_NEXT_PATHS) {
    const rest = legacyHit(pathname, from)
    if (rest !== null) return to + rest + tail
  }
  return raw
}

/** `?next=` 指定的员工端落点；不是员工能去的地方（或本身非法）时返回 null。
 *
 *  三个消费者共用这一份判据：员工栏的登录后落点（`resolveStaffNext`）、面板默认
 *  开在哪一栏（`resolveLoginTab`）。两份白名单：
 *  - 员工前缀（`staffPaths.js` 的 `isStaffLandingPath`）：员工自己的页面。`/register`
 *    不算 —— 账号还没批准、会话也不存在，把 `?next=/register` 当落点就是把员工送进死路；
 *  - **配方阅读面**（票 07）：扫码看岗位配方的那条链路。厨师在员工栏登录、目标是
 *    `/workbench/kitchen/recipe/detail?slug=…` 时，若这里不放行就会**静默落到
 *    `/workbench/me/today`** —— 「登录后回到那条配方」当场失效，而且没有任何报错。
 *    阅读面在清单里是 `both`，员工进得去，所以它是合法的员工落点。
 *
 *  配方**管理**页不在这两份里（那是管理端的一页），别顺手把整个 `/workbench/kitchen/`
 *  前缀放进来。
 */
export function staffNextTarget(raw) {
  const next = sanitizeNext(raw)
  if (!next) return null
  const pathname = pathnameOf(next)
  if (isStaffLandingPath(pathname) || isRecipeReaderPath(pathname)) return next
  return null
}

/** 管理员身份的落点：认站内路径，但拒绝员工端前缀（那是员工 cookie 那扇门）与登录页自身。
 *
 *  两个方向都用同一份判据：员工栏只认员工端路径（`resolveStaffNext`），管理栏不认员工端
 *  路径。
 *
 *  **落点不是管理端能进的页时返回 `fallback`，而调用方给的是「无权访问」页** ——
 *  这里以前硬回落到 `/`：管理端会话打开 `/login?next=/workbench/me/month` 时，`?next=`
 *  被**消费掉却什么也没说**（D4 实测最终落在 `/workbench`，目标消失）。按 ADR 0092
 *  「不静默改道」，这种情况下要带人去 forbidden 并把原目标一起带上 —— 那一页会说明
 *  「这是员工手机端的页面」，还留一条回自己首页的路。默认值仍是 `/`，给那些不想要
 *  这一跳的调用方（以及测试）用。
 */
export function resolveLoginNext(raw, fallback = '/') {
  const fallbackPath = fallback || '/'
  const next = sanitizeNext(raw)
  if (!next) return fallbackPath
  if (isStaffLandingPath(pathnameOf(next))) return fallbackPath
  return next
}

/** 员工栏登录后的落点：`?next=` 只认员工端前缀**与配方阅读面**，别的一律回落到默认落点。
 *
 *  `/register` 是「员工侧界面」却不是落点：账号还没被批准、会话也不存在，把
 *  `?next=/register` 当合法落点就是把员工送进死路。
 *
 *  票 07 起配方阅读面也是合法落点（扫码的厨师登录后回到那条配方）—— 判据与
 *  「面板开哪一栏」共用 `staffNextTarget`，两处不会一个放行一个拒绝。
 *
 *  判据写在 `staffPaths.js` 与 `recipePaths.js`、单测在 `__tests__/loginNext.test.js`，
 *  页面里不许再手写一遍正则 —— 手写的那份比这里弱，放松了也没有断言拦得住。
 */
export function resolveStaffNext(raw, fallback = STAFF_ENTRY_PATH) {
  const fallbackPath = fallback || STAFF_ENTRY_PATH
  return staffNextTarget(raw) || fallbackPath
}

/** 面板默认开在哪一栏。判据按优先级从上到下，第一条命中即用：
 *
 *  1. `?next=` 是**管理端专属页**（页面清单里 `audience === 'admin'`）→ 强制管理员栏。
 *     这类目标只认管理端 cookie，开员工栏等于让人在手机号表单上白填一次（D13 实测：
 *     `/login?next=/workbench/hr/roster` 开的是员工栏，管理端还得自己找 tab）。
 *  2. `?next=` 落在员工端前缀或**配方阅读面**内 → 强制员工栏，优先于记住值
 *     （扫码的绝大多数是厨师，不该先看到管理栏）。
 *  3. 其余（含没有记住值、记住值脏了、站外地址、清单里没有的路径）按记住值走，
 *     没记住过就落员工栏：员工手机上打开 `/login` 就该直接看到手机号那一栏，
 *     不必先点一下（spec 故事 3 / ADR 0091）。管理端机器靠「上次选的是管理员栏」记住
 *     —— 见 `loginPrefs.js` 的 `loadLoginTab`。
 *
 *  身份**只看页面清单那一行**（`router/pageRoutes.js` 的 `pageRow`，唯一来源），不在
 *  这里再写一份前缀正则；清单里没有的路径（老地址、手改的）一律不强制，交回记住值。
 */
export function resolveLoginTab(raw, remembered) {
  if (staffNextTarget(raw)) return 'staff'
  if (adminTarget(raw)) return 'admin'
  return remembered === 'admin' ? 'admin' : 'staff'
}

/** 这条 `?next=` 指向的是不是管理端专属页（`audience === 'admin'`）。
 *
 *  判据取自页面清单：`/workbench/hr/roster`、`/sales-report` 这类页只认管理端 cookie。
 *  `both` 与 `staff` 都不是（配方阅读面对两种身份都开，员工栏就是它的默认栏）。 */
function adminTarget(raw) {
  const next = sanitizeNext(raw)
  if (!next) return false
  const row = pageRow(pathnameOf(next))
  return row?.audience === 'admin'
}
