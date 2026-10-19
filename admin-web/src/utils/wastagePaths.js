/** 日常损耗登记那两个地址（从 worktool2 那个小程序搬过来）。
 *
 *  **一处定义、三处消费**：页面清单（`router/pageRoutes.json`，路径的唯一来源）、工作台
 *  导航（`utils/workbenchNav.js` 的后勤那一格）、以及页面之间的互跳（分析页那颗「去登记」
 *  按钮）。各家再写一份字面量，就是配方搬家时那种「印出去的地址与页面实际地址对不上」
 *  的老毛病。
 *
 *  两页分住两组，是**用的人不同**：员工记一笔在「我的」组（`me`），店长看数与翻明细在
 *  「后勤」组（`kitchen`，与配方、备货计划同组）。
 */

/** 员工端（也是店长能进的那一面）：登记一笔 + 看自己最近记的。 */
export const WASTAGE_REGISTER_PATH = '/workbench/staff/loss'

/** 管理端：按月统计 + 按条件的明细。 */
export const WASTAGE_ANALYSIS_PATH = '/workbench/kitchen/loss'
