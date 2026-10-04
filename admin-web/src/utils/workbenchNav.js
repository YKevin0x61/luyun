/** 工作台外壳的导航（票 03 起）：**一组一格**，按身份过滤。
 *
 *  工作台的路径按组排（`pageRoutes.json` 的 `group`）：我的（员工端，`/workbench/me/*`）
 *  已经落位；人事 / 现场（票 05）、配方 / 备货计划（票 07 / 08）并入时往
 *  `WORKBENCH_NAV_GROUPS` 里各加一行就行 —— 外壳（`views/workbench/WorkbenchLayout.vue`）
 *  只认这张表与身份，不认具体有哪些组，所以加组不用改结构。
 *
 *  **可见性不另写一份名单**：每一格的 `to` 指向清单里的一页，那一页的 `audience` 就是
 *  这一格的可见性（`both` 谁都看得见、`admin` / `staff` 各给一边）。`to` 指不到一页时
 *  `pageMeta` 当场抛错 —— 导航指一条不存在或没登记的路径，员工点下去就是白屏。
 *
 *  **身份认两套词**：清单里的 `audience` 是「允许的身份」三态（`admin` / `staff` / `both`），
 *  顶栏那两档叫 `super` / `staff`（票 04 的「工作台身份」，`super` 是管理端那个共享账号
 *  —— 不叫「管理员」）。两者的映射就在 `workbenchAudienceFor` 这一处，别在别处再写一遍。
 */
import { pageMeta, pageRow } from '../router/pageRoutes.js'
import { IDENTITY_ADMIN } from './workbenchIdentity.js'

export const WORKBENCH_NAV_GROUPS = [
  // 员工端三页（今天 / 整月 / 卫生待办）同属「我的」这一组，落点是「今天」。
  { key: 'me', label: '我的', to: '/workbench/me/today' },
]

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
