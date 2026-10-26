/** 工作台外壳的导航（2026-10-19 信息架构重排第二层）：**两套显式表**。
 *
 *  拆表的原因：原先只有一张按身份过滤的合并表，6 格里混着「页」与「组」，
 *  名字也跟内容对不上 —— `floor` 叫「卫生验收」却只是现场八页那一组、`kitchen` 叫「配方」
 *  却装着备货计划与损耗分析。现在管理端看 `ADMIN_NAV_GROUPS`（今天 / 人事 / 现场 / 后勤），
 *  员工端看 `STAFF_NAV_GROUPS`（今天 / 人事 / 卫生 / 后勤 / 我的）。两套表共享同一批组定义，
 *  不复制第二份；**过滤判据仍是 `pageVisibleFor` 一处**，所以拆表前后渲染结果一致
 *  （员工勾了「排班管理」能力时，`hr` 那一格才出现 —— 员工表里留着它、由能力键过滤）。
 *
 *  **可见性不另写一份名单**：每一格的 `to`（以及 `links` 里的每一条）指向清单里的一页，
 *  那一页的 `audience`（以及员工那一档的 `staffCap`）就是这一项的可见性（`both` 谁都看得见、
 *  `admin` / `staff` 各给一边、`admin + staffCap` 给「勾了那项能力的员工」）。判据只有
 *  `pageVisibleFor` 一处。`to` 指不到一页时 `pageMeta` 仍当场抛错 —— 导航指一条不存在或
 *  没登记的路径，员工点下去就是白屏。
 *
 *  **一格可以有多扇门**（后勤）：`links` 是这一格要渲染的全部条目；`to` 仍是这一格的主落点
 *  （别的壳做跨组门时用的那一个）。外壳只认 `workbenchLinksOf` 一个口，不自己判 `links` 有没有。
 *  整格高亮按页面清单的 `group` 判（`workbenchGroupOf`），不按路径前缀比。
 *
 *  **身份认两套词**：清单里的 audience 是「允许的身份」三态（`admin` / `staff` / `both`），
 *  顶栏那两档叫 `super` / `staff`（`super` 是管理端那个共享账号，不叫「管理员」）。两者的
 *  映射就在 `workbenchAudienceFor` 这一处，别在别处再写一遍。
 */
import { PAGE_ROUTES, pageMeta, pageRow } from '../router/pageRoutes.js'
import { hasCap } from './adminCaps.js'
import { PREP_PLAN_PATH, PREP_REVIEW_PATH } from './prepPlanPaths.js'
import { RECIPE_HOME_PATH, RECIPE_MANAGE_PATH, RECIPE_QR_PATH } from './recipePaths.js'
import { WASTAGE_ANALYSIS_PATH } from './wastagePaths.js'
import { WORKBENCH_FIELD_HOME, WORKBENCH_HOME, WORKBENCH_HR_HOME, WORKBENCH_TITLE } from './workbenchCopy.js'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from './workbenchIdentity.js'

// 两套表共享的组定义（同一批对象，不复制第二份）：
// 首页「今天」两档都看得见；人事两档都有（员工那一档靠「排班管理」能力键过滤，见下）；
// 后勤那一格是「一组多门」（配方 / 备货计划 / 损耗分析），两档共用同一份 links 与 pages。
const HOME_GROUP = {
  // 首页（票 06）：「今天」—— 子应用根，两种身份都看得见（那一页在清单里是 `both`）。
  // `headLabel`：手机档页头里**组名那一格**写什么。这一组的 `label` 与它唯一那一页的标题
  // 都是「今天」，页头里并排写两遍是废话 —— 组名那格写「工作台」（它是子应用首页）。
  key: 'home', label: '今天', headLabel: WORKBENCH_TITLE, to: WORKBENCH_HOME,
}

const HR_GROUP = {
  // 人事：月历 / 待办 / 班次表 / 花名册 …（落点是月历，组里的页由 `workbenchPagesOf` 给）。
  key: 'hr', label: '人事', to: WORKBENCH_HR_HOME,
}

const KITCHEN_GROUP = {
  // 后勤：配方（票 07）+ 备货计划（票 08）+ 损耗分析 —— **一组一格、一格多门**。几条落点
  // 都是清单里的一页；`to` 仍是这一格的主落点（也是别的壳做跨组门时用的那一个），`links`
  // 是这一格要渲染的全部条目。落点常量住在 `utils/recipePaths.js` / `prepPlanPaths.js` /
  // `wastagePaths.js`，与页面里的 router-link 同一份。
  key: 'kitchen',
  label: '后勤',
  to: RECIPE_HOME_PATH,
  links: [
    { to: RECIPE_HOME_PATH },
    { to: PREP_PLAN_PATH },
    // 备货复盘（ADR 0123）：每小时参考值快照 vs 实际销量，改公式前先看它。
    // 员工那一档看不到它（清单里 `audience: admin`）。
    { to: PREP_REVIEW_PATH },
    // 损耗分析（从 worktool2 那个小程序搬过来）：店长按月看「这个月损耗了多少、什么
    // 原因最多、哪几样最费」，是天天会看的数，不是配置页 —— 所以给它一扇直达的门。
    // 员工那一档看不到它（清单里 `audience: admin`）。
    { to: WASTAGE_ANALYSIS_PATH },
  ],
  // 组内**一级入口**（手写）：`workbenchPagesOf` 默认从页面清单派生，而清单里
  // 「配方详情」（要带 `?slug=`）与「配方打印」（A4 预览）是**从列表点进去**的功能页，
  // 不是能当入口的页 —— 放进页头那个下拉就是两扇点不通的死门（2026-10-08 实测，
  // 派生出来是六项）。名单里只写路径，标题仍从清单取。
  pages: [
    RECIPE_HOME_PATH, RECIPE_QR_PATH, RECIPE_MANAGE_PATH,
    PREP_PLAN_PATH, PREP_REVIEW_PATH, WASTAGE_ANALYSIS_PATH,
  ],
}

/** 管理端一级导航：4 组（今天 / 人事 / 现场 / 后勤）。 */
export const ADMIN_NAV_GROUPS = [
  HOME_GROUP,
  HR_GROUP,
  // 现场：卫生八页，**超级管理员那一档**的面（落点是日常验收，到了那儿由那一组自己的
  // rail 接手）。员工看不到这一格（落点那一页的 `audience` 是 `admin`）；员工做卫生走
  // `STAFF_NAV_GROUPS` 里的「卫生」。
  { key: 'floor', label: '现场', to: WORKBENCH_FIELD_HOME },
  KITCHEN_GROUP,
]

/** 员工端一级导航：4 格（今天 / 卫生 / 后勤 / 我的）+ 一格能力键页。
 *
 *  「卫生」这一格（2026-10-05 用户裁定）：两个身份做的卫生是同一批活，但看到的是各自的面
 *  —— 超级管理员走 `floor`（`/workbench/floor/*` 八页，管验收与整改），员工走这一格
 *  （`/workbench/staff/clean`：今天要做几项、逐项拍照交）。它原来藏在「我的」下面，员工
 *  要点两层才到，而这是他们每天最常干的事 —— 给它一个一级入口。
 *
 *  `hr` 留在这张表里是为了保留「勾了排班管理的员工能进人事三页」的既有行为（2026-10-14）：
 *  落点页是 `admin + staffCap: schedule`，没勾能力键时被 `pageVisibleFor` 过滤掉，默认只
 *  渲染 4 格。**不要**为了好看把它删掉 —— 删了员工那一档就再也进不去排班月历。
 */
export const STAFF_NAV_GROUPS = [
  HOME_GROUP,
  HR_GROUP,
  { key: 'hygiene', label: '卫生', to: '/workbench/staff/clean' },
  KITCHEN_GROUP,
  // 员工端的「今天 / 整月」同属「我的」这一组，落点是「今天」。
  { key: 'me', label: '我的', to: '/workbench/staff/today' },
]

/** 身份（`super` / `staff`，也接受清单词 `admin`）→ 该看的那一张表；认不出身份给空表。 */
function navGroupsFor(identity) {
  if (identity === IDENTITY_ADMIN || identity === 'admin') return ADMIN_NAV_GROUPS
  if (identity === IDENTITY_STAFF) return STAFF_NAV_GROUPS
  return []
}

/** 清单身份词（`admin` / `staff`）→ 表；不给身份（老调用点、单测）时两套都查。 */
function navGroupsForAudience(audience) {
  if (audience === 'admin') return ADMIN_NAV_GROUPS
  if (audience === 'staff') return STAFF_NAV_GROUPS
  return [...ADMIN_NAV_GROUPS, ...STAFF_NAV_GROUPS]
}

/** 那一组那一行（外壳做跨组的门用：门牌与落点都从这儿来，不写第二份）。
 *
 *  身份可选：给了就只查对应那一张表，不给就两张都查（`floor` / `hygiene` 本来就各只在
 *  一张表里；`home` / `hr` / `kitchen` 两套共享同一个对象）。 */
export function workbenchGroup(key, identity) {
  const groups = identity === undefined || identity === null
    ? [...ADMIN_NAV_GROUPS, ...STAFF_NAV_GROUPS]
    : navGroupsFor(identity)
  return groups.find((group) => group.key === key) || null
}

/** 这一格要渲染的条目：**有 `links` 用 `links`，没有就是 `to` 自己**。
 *
 *  单条目那一格因此与票 07 之前一字不差（返回 `[{ to }]`），多条目那一格拿到全部条目 ——
 *  外壳只认这一个口，不再自己判 `links` 有没有。 */
export function workbenchLinksOf(group) {
  return group.links ? [...group.links] : [{ to: group.to }]
}

/** 这一页对**这个身份 + 这组能力**可不可见（导航与组内导航的唯一判据）。
 *
 *  三态 `audience` 之外还有 `staffCap`（2026-10-14）：店长专属页（`audience: admin`）可以
 *  对「勾了某项能力的员工」也开一扇门 —— 排班三页（`schedule`）与损耗分析（`wastage`）。
 *
 *  判定顺序（fail-closed）：
 *  1. `both` 谁都看得见；
 *  2. `audience` 与这一档相同 —— 管理端看 admin 页，员工看 staff 页；
 *  3. 员工这一档再看 `staffCap`：勾了这一项就开，没勾就不开（管理端的 `admin` 页
 *     对管理端永远开着，不看 `staffCap`）；
 *  4. 其余不可见。
 *
 *  清单里没登记的路径（单测里的临时路由）按可见处理 —— 与这条落地之前一字不差。
 */
export function pageVisibleFor(row, audience, caps) {
  if (!row) return true
  const allowed = row.audience
  if (allowed === 'both') return true
  if (allowed === audience) return true
  if (audience === 'staff' && row.staffCap) return hasCap(caps, row.staffCap)
  return false
}

/** 组里有哪些页（路径 + 标题，顺序照页面清单）—— 壳的组内导航从它派生。
 *  清单是唯一来源：组里加一页只改那张表，导航自己就跟上。
 *
 *  **两层过滤（③-2 / spec §5.13）**：`group` 那层之外，还要按**落点页的 `audience`**
 *  （以及员工那一档的 `staffCap`）过一遍 —— 只按 group 过滤会让员工在「配方」组看到
 *  「岗位二维码 / 配方管理」两扇点不通的门，也会让没勾「排班管理」的员工在人事组
 *  看到月历 / 待办 / 班次表三扇同样点不通的门（手写名单 `pages` 与清单派生两条路都要过
 *  这一层）。`audience` 不给（老调用点、单测里那种临时路由）时只按 group 过滤，与这一条
 *  落地之前一字不差。 */
export function workbenchPagesOf(group, audience, caps) {
  const visible = (path) => {
    if (!audience) return true
    return pageVisibleFor(pageRow(path), audience, caps)
  }
  // 手写的入口名单优先（见 `kitchen` 那一格的 `pages`）：清单里有些页是「从列表点进去」的
  // 功能页，当一级入口就是点不通的死门。名单里只写路径，标题仍从清单取 —— 一处定义。
  const cell = navGroupsForAudience(audience).find((item) => item.key === group)
  if (cell && cell.pages) {
    return cell.pages
      .filter((path) => visible(path))
      .map((path) => {
        const row = pageRow(path)
        return { path, title: row ? row.title : path }
      })
  }
  return PAGE_ROUTES.filter((row) => row.group === group)
    .filter((row) => visible(row.path))
    .map(({ path, title }) => ({ path, title }))
}

/** 工作台身份（`super` / `staff`）→ 清单里的身份词（`admin` / `staff`）。
 *  认不出的身份给 `null`（不是 `admin`）：调用方据此不渲染任何一格，fail-closed。 */
export function workbenchAudienceFor(identity) {
  if (identity === IDENTITY_ADMIN) return 'admin'
  if (identity === IDENTITY_STAFF) return 'staff'
  return null
}

/** 身份 + 能力键 → 导航项（一项 = 一格里的一个链接，`key` 是它所属的那一格）。
 *
 *  **过滤按项、不按格**：每条链接的可见性只看它自己落点那一页的 `audience`（员工那一档
 *  再看 `staffCap`，判据是 `pageVisibleFor` 一处）；整格一起藏的反面（组里一页给管理端、
 *  一页给两边）也就自然成立 —— 后勤那一格的后半扇门对员工可见（票 08 的验收），而
 *  「配方管理」那样的 admin 页不在 `links` 里，也就永远不会被渲染出来。
 *  身份认不出（没登录、探针还没回来）时不渲染任何一格，宁可没有导航，也不给一个
 *  点不通的入口。`caps` 只对员工那一档有用（管理端不看能力键）。
 */
export function workbenchNavFor(identity, caps) {
  const audience = workbenchAudienceFor(identity)
  if (!audience) return []
  return navGroupsFor(identity).flatMap((group) =>
    workbenchLinksOf(group)
      .filter(({ to }) => pageVisibleFor(pageMeta(to), audience, caps))
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
