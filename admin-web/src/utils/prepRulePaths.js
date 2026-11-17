/** 备货品换算规则那一页的地址（2026-10-26 从销售报表的 tab 搬进后勤组）。
 *
 * 2026-11-17 起收进备货计划的模块 tab，作为 `/prep-plan` 的子路由；
 * 旧 `/prep-rules` 自然 404。
 *
 * 为什么放成常量而不是各写一遍：路由（`router/index.js`）、工作台导航
 * （`utils/workbenchNav.js` 的后勤那一格）、库存行里的「改换算」入口都指同一页。
 */
export const PREP_RULES_PATH = '/workbench/kitchen/prep-plan/rules'
