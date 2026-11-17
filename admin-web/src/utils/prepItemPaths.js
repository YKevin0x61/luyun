/** 备货品主数据那一页的地址（2026-10-27 新增）。
 *
 * 2026-11-17 起收进备货计划的模块 tab，作为 `/prep-plan` 的子路由；
 * 旧 `/prep-items` 自然 404。
 *
 * 为什么放常量：路由、工作台后勤那一格、换算规则页「数据管理」的跳转都指同一页。
 */
export const PREP_ITEMS_PATH = '/workbench/kitchen/prep-plan/items'
