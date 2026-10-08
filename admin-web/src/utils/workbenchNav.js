/** 工作台外壳的导航（票 03 起）：**一组一格**，按身份过滤。
 *
 *  工作台的路径按组排（`pageRoutes.json` 的 `group`）：人事（`/workbench/hr/*`）、现场
 *  （`/workbench/floor/*`）票 05 落位，「我的」（`/workbench/me/*`）票 03 就在，后勤
 *  （`/workbench/kitchen/*`：配方票 07、备货计划票 08）随后进来 —— 外壳
 *  （`views/workbench/WorkbenchLayout.vue`）只认这张表与身份，不认具体有哪些组，所以加组
 *  不用改结构。两个管理端壳（人事壳 / 现场壳）也引这张表：它们的跨组门（「现场 ›」/
 *  「人事」）的落点与名字就是这里那一行，不各写一遍。
 *
 *  **可见性不另写一份名单**：每一格的 `to`（以及票 08 起 `links` 里的每一条）指向清单里
 *  的一页，那一页的 `audience` 就是这一项的可见性（`both` 谁都看得见、`admin` / `staff`
 *  各给一边）。`to` 指不到一页时 `pageMeta` 当场抛错 —— 导航指一条不存在或没登记的路径，
 *  员工点下去就是白屏。
 *
 *  **一组一格，但一格可以有两扇门**（票 08）：后勤组装着配方与备货计划两页，员工导航里
 *  两项都要有（spec 故事 8）。做法是给那一格加 `links`（**一组多条目**），而不是把后勤
 *  拆成两格 —— 拆成两格的话 `workbenchGroupOf('/workbench/kitchen/prep-plan')` 仍然返回
 *  `kitchen`，两格会**同时高亮**，看上去像两个「后勤」分组。于是外壳渲染的是「格子」：
 *  单条目那一格还是一个链接（与票 07 之前一字不差，标签取 `label`），多条目那一格是同一
 *  颗胶囊里的两个链接（`is-multi`），每一半的标签取**它自己那一页在清单里的标题**
 *  （同一颗胶囊里写两遍「后勤」看不出哪一半是哪一页）。整格高亮仍由 `workbenchGroupOf`
 *  判（页面的 `group`，不按路径前缀比），哪一半贴着"当前"由路径逐条判（见外壳）。
 *
 *  **身份认两套词**：清单里的 `audience` 是「允许的身份」三态（`admin` / `staff` / `both`），
 *  顶栏那两档叫 `super` / `staff`（票 04 的「工作台身份」，`super` 是管理端那个共享账号
 *  —— 不叫「管理员」）。两者的映射就在 `workbenchAudienceFor` 这一处，别在别处再写一遍。
 */
import { PAGE_ROUTES, pageMeta, pageRow } from '../router/pageRoutes.js'
import { PREP_PLAN_PATH } from './prepPlanPaths.js'
import { RECIPE_HOME_PATH, RECIPE_MANAGE_PATH, RECIPE_QR_PATH } from './recipePaths.js'
import { WORKBENCH_FIELD_HOME, WORKBENCH_HOME, WORKBENCH_HR_HOME, WORKBENCH_TITLE } from './workbenchCopy.js'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from './workbenchIdentity.js'

export const WORKBENCH_NAV_GROUPS = [
  // 首页（票 06）：「今天」—— 子应用根，两种身份都看得见（那一页在清单里是 `both`）。
  // `headLabel`：手机档页头里**组名那一格**写什么。这一组的 `label` 与它唯一那一页的标题
  // 都是「今天」，页头里并排写两遍是废话 —— 组名那格写「工作台」（它是子应用首页），
  // 与「卫生 › 日常验收」「产品 › 选择岗位」同一个读法（2026-10-08 用户裁定）。
  { key: 'home', label: '今天', headLabel: WORKBENCH_TITLE, to: WORKBENCH_HOME },
  // 人事：月历 / 待办 / 班次表 / 花名册（落点是月历，组里的页由 `workbenchPagesOf` 给）。
  { key: 'hr', label: '人事', to: WORKBENCH_HR_HOME },
  // 卫生（组名 2026-10-08 由用户裁定，从「现场」改过来）：卫生八页，**店长那一档**的面
  // （落点是日常验收，到了那儿由那一组自己的导航接手）。
  // 员工看不到这一格（落点那一页的 `audience` 是 `admin`）；员工做卫生走下面那一格。
  // **两格的显示名现在都是「卫生」**（上面这一格 `floor`、下面那一格 `hygiene`）：同一批活
  // 的两张面，靠 `key` 与 `audience` 区分，不靠名字 —— 每次只有一个身份在渲染，界面上
  // 不会同时出现两个「卫生」。
  { key: 'floor', label: '卫生', to: WORKBENCH_FIELD_HOME },
  // 员工端的**卫生**这一格（2026-10-05 用户裁定）：两个身份做的卫生是同一批活，但看到的是
  // 各自的面 —— 店长走上一格（`/workbench/floor/*` 八页，管验收与整改），员工走这一格
  // （`/workbench/me/clean`：今天要做几项、逐项拍照交）。它原来藏在「我的」下面，员工要点
  // 两层才到，而这是他们每天最常干的事 —— 给它一个一级入口。
  // 可见性仍由落点那一页的 `audience`（`staff`）决定：店长那一档自然看不到这一格。
  { key: 'hygiene', label: '卫生', to: '/workbench/me/clean' },
  // 后勤：配方（票 07）+ 备货计划（票 08）—— **一组一格、一格两门**。两条落点都是清单里
  // 的一页（`both`，所以员工这一档两半都看得见）；`to` 仍是这一格的主落点（也是别的壳做
  // 跨组门时用的那一个），`links` 是这一格要渲染出来的全部条目。落点常量分别住在
  // `utils/recipePaths.js` 与 `utils/prepPlanPaths.js`，与页面里的 router-link 同一份。
  {
    key: 'kitchen',
    label: '产品',
    to: RECIPE_HOME_PATH,
    links: [
      { to: RECIPE_HOME_PATH },
      { to: PREP_PLAN_PATH },
    ],
    // 组内**一级入口**（手写）：`workbenchPagesOf` 默认从页面清单派生，而清单里
    // 「配方详情」（要带 `?slug=`）与「配方打印」（A4 预览）是**从列表点进去**的功能页，
    // 不是能当入口的页 —— 放进页头那个下拉就是两扇点不通的死门（2026-10-08 实测，
    // 派生出来是六项）。名单里只写路径，标题仍从清单取。
    pages: [RECIPE_HOME_PATH, RECIPE_QR_PATH, RECIPE_MANAGE_PATH, PREP_PLAN_PATH],
  },
  // 员工端的「今天 / 整月」同属「我的」这一组，落点是「今天」。
  // （卫生那一页已经挪到上面独立的「卫生」格；「我的」组现在只剩今天与整月两页。）
  { key: 'me', label: '我的', to: '/workbench/me/today' },
]

/** 那一组那一行（外壳做跨组的门用：门牌与落点都从这儿来，不写第二份）。 */
export function workbenchGroup(key) {
  return WORKBENCH_NAV_GROUPS.find((group) => group.key === key) || null
}

/** 这一格要渲染的条目：**有 `links` 用 `links`，没有就是 `to` 自己**。
 *
 *  单条目那一格因此与票 07 之前一字不差（返回 `[{ to }]`），多条目那一格拿到全部条目 ——
 *  外壳只认这一个口，不再自己判 `links` 有没有。 */
export function workbenchLinksOf(group) {
  return group.links ? [...group.links] : [{ to: group.to }]
}

/** 组里有哪些页（路径 + 标题，顺序照页面清单）—— 壳的组内导航从它派生。
 *  清单是唯一来源：组里加一页只改那张表，导航自己就跟上。 */
export function workbenchPagesOf(group) {
  // 手写的入口名单优先（见 `kitchen` 那一格的 `pages`）：清单里有些页是「从列表点进去」的
  // 功能页，当一级入口就是点不通的死门。名单里只写路径，标题仍从清单取 —— 一处定义。
  const cell = WORKBENCH_NAV_GROUPS.find((item) => item.key === group)
  if (cell && cell.pages) {
    return cell.pages.map((path) => {
      const row = pageRow(path)
      return { path, title: row ? row.title : path }
    })
  }
  return PAGE_ROUTES.filter((row) => row.group === group).map(({ path, title }) => ({ path, title }))
}

/** 工作台身份（`super` / `staff`）→ 清单里的身份词（`admin` / `staff`）。
 *  认不出的身份给 `null`（不是 `admin`）：调用方据此不渲染任何一格，fail-closed。 */
export function workbenchAudienceFor(identity) {
  if (identity === IDENTITY_ADMIN) return 'admin'
  if (identity === IDENTITY_STAFF) return 'staff'
  return null
}

/** 身份 → 导航项（一项 = 一格里的一个链接，`key` 是它所属的那一格）。
 *
 *  **过滤按项、不按格**：每条链接的可见性只看它自己落点那一页的 `audience`；整格一起藏的
 *  反面（组里一页给管理端、一页给两边）也就自然成立 —— 后勤那一格的后半扇门对员工可见
 *  （票 08 的验收），而「配方管理」那样的 admin 页不在 `links` 里，也就永远不会被渲染
 *  出来。身份认不出（没登录、探针还没回来）时不渲染任何一格，宁可没有导航，也不给一个
 *  点不通的入口。 */
export function workbenchNavFor(identity) {
  const audience = workbenchAudienceFor(identity)
  if (!audience) return []
  return WORKBENCH_NAV_GROUPS.flatMap((group) =>
    workbenchLinksOf(group)
      .filter(({ to }) => {
        const { audience: allowed } = pageMeta(to)
        return allowed === 'both' || allowed === audience
      })
      .map(({ to }) => ({ key: group.key, label: group.label, to }))
  )
}

/** 当前路径属于哪一组（外壳据此高亮）。
 *
 *  **不能靠 `router-link` 自己的 active 类**：一格指向整组，而 `to` 只是组里的一页 ——
 *  站在「整月」上时指向「今天」的那条链接按路径比是不会亮的。高亮的判据因此是
 *  「这一页的 group」，与导航项自己的 key 是同一个词表。
 *
 *  票 08 起这一条更值钱：后勤那一格里备货计划的链接指着 `/workbench/kitchen/prep-plan`，
 *  高亮仍按 `kitchen` 算 —— 不按路径前缀比，也就不会因为组里多一页而漂。 */
export function workbenchGroupOf(pathname) {
  const row = pageRow(pathname)
  return row ? row.group : null
}
