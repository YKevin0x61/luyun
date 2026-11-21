/** 厨房采购的固定地址（从 worktool2 小程序搬来，2026-11-21）。
 *
 *  页面清单与路由都以这些字面量为准；组件之间跳转不要各写一遍字符串 ——
 *  「今天 / 详情」这类入口一多，路径一改就会漂成两套（仓库里配方、备货、损耗
 *  都走过同一条路）。
 *
 *  详情页用静态路径 + ?id=：tests/test_spa_page_routes.py 只解析字面量路径，
 *  动态 :id 解析不出来（仓库先例是配方详情 ?slug=）。
 */
export const PURCHASE_HOME_PATH = '/workbench/kitchen/purchase'
export const PURCHASE_ORDERS_PATH = '/workbench/kitchen/purchase/orders'
export const PURCHASE_ORDER_DETAIL_PATH = '/workbench/kitchen/purchase/order'
export const PURCHASE_CATALOG_PATH = '/workbench/kitchen/purchase/catalog'
export const PURCHASE_ANALYSIS_PATH = '/workbench/kitchen/purchase/analysis'
export const PURCHASE_MIGRATION_PATH = '/workbench/kitchen/purchase/import'

/** 订单详情地址：只接受正整数，脏 id 返回空串（调用方按「没有这一单」处理）。 */
export function purchaseOrderPath(orderId) {
  const id = Number(orderId)
  return Number.isInteger(id) && id > 0 ? PURCHASE_ORDER_DETAIL_PATH + '?id=' + id : ''
}
