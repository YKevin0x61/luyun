/** 工作台外壳的导航（票 03 起）：**一组一格**，按身份过滤。
 *
 *  工作台的路径按组排（`pageRoutes.json` 的 `group`）：人事（`/workbench/hr/*`）、现场
 *  （`/workbench/floor/*`）票 05 落位，「我的」（`/workbench/me/*`）票 03 就在；配方 /
 *  备货计划（票 07 / 08）并入时往 `WORKBENCH_NAV_GROUPS` 里各加一行就行 —— 外壳
 *  （`views/workbench/WorkbenchLayout.vue`）只认这张表与身份，不认具体有哪些组，所以加组
 *  不用改结构。两个管理端壳（人事壳 / 现场壳）也引这张表：它们的跨组门（「现场 ›」/
 *  「人事」）的落点与名字就是这里那一行，不各写一遍。
 *
 *  **可见性不另写一份名单**：每一格的 `to` 指向清单里的一页，那一页的 `audience` 就是
 *  这一格的可见性（`both` 谁都看得见、`admin` / `staff` 各给一边）。`to` 指不到一页时
 *  `pageMeta` 当场抛错 —— 导航指一条不存在或没登记的路径，员工点下去就是白屏。
 *
 *  **身份认两套词**：清单里的 `audience` 是「允许的身份」三态（`admin` / `staff` / `both`），
 *  顶栏那两档叫 `super` / `staff`（票 04 的「工作台身份」，`super` 是管理端那个共享账号
 *  —— 不叫「管理员」）。两者的映射就在 `workbenchAudienceFor` 这一处，别在别处再写一遍。
 */
import { PAGE_ROUTES, pageMeta, pageRow } from '../router/pageRoutes.js'
import { WORKBENCH_FIELD_HOME, WORKBENCH_HOME, WORKBENCH_HR_HOME } from './workbenchCopy.js'
import { IDENTITY_ADMIN } from './workbenchIdentity.js'

export const WORKBENCH_NAV_GROUPS = [
  // 首页（票 06）：「今天」—— 子应用根，两种身份都看得见（那一页在清单里是 `both`）。
  { key: 'home', label: '今天', to: WORKBENCH_HOME },
  // 人事：月历 / 待办 / 班次表 / 花名册（落点是月历，组里的页由 `workbenchPagesOf` 给）。
  { key: 'hr', label: '人事', to: WORKBENCH_HR_HOME },
  // 现场：卫生七页（落点是日常验收，到了那儿由那一组自己的 rail 接手）。
  { key: 'floor', label: '现场', to: WORKBENCH_FIELD_HOME },
  // 员工端三页（今天 / 整月 / 卫生待办）同属「我的」这一组，落点是「今天」。
  { key: 'me', label: '我的', to: '/workbench/me/today' },
]

/** 那一组那一行（外壳做跨组的门用：门牌与落点都从这儿来，不写第二份）。 */
export function workbenchGroup(key) {
  return WORKBENCH_NAV_GROUPS.find((group) => group.key === key) || null
}

/** 组里有哪些页（路径 + 标题，顺序照页面清单）—— 壳的组内导航从它派生。
 *  清单是唯一来源：组里加一页只改那张表，导航自己就跟上。 */
export function workbenchPagesOf(group) {
  return PAGE_ROUTES.filter((row) => row.group === group).map(({ path, title }) => ({ path, title }))
}

/** 工作台身份（`super` / `staff`）→ 清单里的身份词（`admin` / `staff`）。
 *  认不出的身份给 `null`（不是 `admin`）：调用方据此不渲染任何一格，fail-closed。 */
export function workbenchAudienceFor(identity) {
  if (identity === IDENTITY_ADMIN) return 'admin'
  if (identity === 'staff') return 'staff'
  return null
}

/** 身份 → 导航项。身份认不出（没登录、探针还没回来）时不渲染任何一格，
 *  宁可没有导航，也不给一个点不通的入口。 */
export function workbenchNavFor(identity) {
  const audience = workbenchAudienceFor(identity)
  if (!audience) return []
  return WORKBENCH_NAV_GROUPS.filter(({ to }) => {
    const { audience: allowed } = pageMeta(to)
    return allowed === 'both' || allowed === audience
  })
}

/** 当前路径属于哪一组（外壳据此高亮）。
 *
 *  **不能靠 `router-link` 自己的 active 类**：一格指向整组，而 `to` 只是组里的一页 ——
 *  站在「整月」上时指向「今天」的那条链接按路径比是不会亮的。高亮的判据因此是
 *  「这一页的 group」，与导航项自己的 key 是同一个词表。
 */
export function workbenchGroupOf(pathname) {
  const row = pageRow(pathname)
  return row ? row.group : null
}
