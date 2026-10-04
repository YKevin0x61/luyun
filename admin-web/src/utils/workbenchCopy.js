/** 「工作台」：排班 + 卫生合并之后的那个子系统（2026-10-04 用户定名）。
 *
 *  名字挑的是**容器**，不是内容：现在装着两组——**人事**（排班、加班、绩效、健康证）、
 *  **现场**（卫生、工作区、验收、整改），以后还要装**后勤**（日常采购）。所以这里只放
 *  子系统自己的用语与分组，不列任何一组的具体页面清单（那些归各组的导航）。
 *
 *  两组同住 `/workbench/*`（票 04 收口：旧的 `/hygiene/*` 与 `/scheduling*` 已删干净）。
 *  `WORKBENCH_FIELD_HOME` 指现场那一组的第一页，到了那儿由那一组自己的 rail 接手。
 */
export const WORKBENCH_TITLE = '工作台'
export const WORKBENCH_TAGLINE = '人事 · 现场 · 后勤'

/** 「现场」那一组的落点：它自己的第一页（花名册），到了那儿由那一组的导航接手。 */
export const WORKBENCH_FIELD_HOME = '/workbench/roster'

/** 工作台**首页**（仪表盘上那张卡通往哪儿）：先落在排班月历 —— 店长每天先看"今天谁上班"。 */
export const WORKBENCH_HOME = '/workbench'

/** 仪表盘那张卡的说明：两组一起说，别只列现场那一组。 */
export const WORKBENCH_DASHBOARD_BLURB = '当班排班 · 现场验收 · 整改 · 仪容仪表 · 红黑榜'
