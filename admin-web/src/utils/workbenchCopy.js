/** 「工作台」：排班 + 卫生合并之后的那个子系统（2026-10-04 用户定名）。
 *
 *  名字挑的是**容器**，不是内容：现在装着两组——**人事**（排班、加班、绩效、健康证）、
 *  **现场**（卫生、工作区、验收、整改），以后还要装**后勤**（日常采购）。所以这里只放
 *  子系统自己的用语与分组，不列任何一组的具体页面清单（那些归各组的导航）。
 *
 *  票 05 起路径按组排（`admin-web/src/router/pageRoutes.json` 的 `group`）：人事
 *  `/workbench/hr/*`、现场 `/workbench/floor/*`、我的 `/workbench/me/*`。两个壳
 *  （`views/scheduling/SchedulingLayout.vue` 与 `views/hygiene/HygieneAdminLayout.vue`）
 *  互相开门，门牌就是这里两个**落点**常量 —— 一处定义，两个壳与页内跳转都引它。
 */
export const WORKBENCH_TITLE = '工作台'
export const WORKBENCH_TAGLINE = '人事 · 现场 · 后勤'

/** 「人事」那一组的落点：排班月历（店长每天先看"今天谁上班"）。 */
export const WORKBENCH_HR_HOME = '/workbench/hr/calendar'

/** 「现场」那一组的落点：日常验收（品牌那句「对照实拍验收」说的就是这一页）。
 *  到了那儿由那一组自己的 rail 接手。 */
export const WORKBENCH_FIELD_HOME = '/workbench/floor/daily'

/** 工作台**首页**（仪表盘上那张卡通往哪儿）：子应用根 `/workbench` —— 票 06 起它就是
 *  「今天」：店长看到今天谁上班与三个数字，员工看到自己的班、工作区与待办条数。
 *  它与「人事」那一组的落点（月历）是两个地址，不再互相兼职。 */
export const WORKBENCH_HOME = '/workbench'

/** 仪表盘那张卡的说明：两组一起说，别只列现场那一组。 */
export const WORKBENCH_DASHBOARD_BLURB = '当班排班 · 现场验收 · 整改 · 仪容仪表 · 红黑榜'

/** 页面标题的统一写法：`<页面名> · 工作台`。
 *
 *  规格只写一次 —— 工作台里那几个壳（首页 / 我的共用的工作台壳、人事壳、现场壳）与配方的
 *  阅读面都给页面挂标题，各写各的模板迟早漂成两种写法（`hygieneCopy.js` 的
 *  `hygieneDocumentTitle` 转发到这里）。
 *
 *  页面名本身就是「工作台」时只留一个，不写「工作台 · 工作台」（票 06 起子应用根那页叫
 *  「今天」，这条只留给调用方传进「工作台」的情形）。 */
export function workbenchDocumentTitle(pageName) {
  const name = pageName == null ? '' : String(pageName).trim()
  if (!name || name === WORKBENCH_TITLE) return WORKBENCH_TITLE
  return `${name} · ${WORKBENCH_TITLE}`
}
