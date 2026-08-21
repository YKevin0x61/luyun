/**
 * 菜品分组聚合工具（共享）
 *
 * 抽取自原 stores/dishes.js（仪表盘用，按 菜品+档口 分组、跨全部状态）与
 * pages/kitchen/kitchen.vue（厨房页用，按 菜名+备注 分组、仅当前档口待出餐订单）中
 * 重复实现的"按分组键聚合订单、累加数量、收集订单列表"逻辑。
 *
 * 本工具只负责分组这一共同步骤；等待时长/紧急数/最早下单时间等派生字段由
 * 各消费方按自身口径在分组结果之上继续计算，口径不因此改变。
 */

import { canonicalOrderNotes, dishNotesIdentityKey } from './orderNotes.js'

/**
 * 按 keyFn 返回的分组键，将订单聚合为菜品维度分组
 * @param {Array} orders 订单列表（调用方负责过滤掉无效订单）
 * @param {Function} keyFn 分组键函数：(order) => string
 * @returns {Object} 以分组键为下标的聚合结果：{ [key]: { dishName, station, totalQuantity, orders } }
 */
export function groupOrdersByDish(orders, keyFn) {
  const groups = {}

  orders.forEach(order => {
    const key = keyFn(order)

    if (!groups[key]) {
      groups[key] = {
        dishName: order.dish_name,
        station: order.station,
        totalQuantity: 0,
        orders: []
      }
    }

    groups[key].totalQuantity += (order.quantity || 0)
    groups[key].orders.push(order)
  })

  return groups
}

/**
 * Kitchen 菜卡 identity: 同一菜名同一归一备注.
 * Dashboard stats still call groupOrdersByDish with 菜品+档口 and must not use this.
 *
 * @param {Array} orders
 * @returns {Array<{ dishName: string, station: string, notes: string, totalQuantity: number, orders: object[] }>}
 */
export function groupOrdersByDishNotes(orders) {
  const groups = groupOrdersByDish(orders, (order) =>
    dishNotesIdentityKey(order.dish_name, order.notes)
  )
  return Object.values(groups).map((group) => ({
    ...group,
    notes: canonicalOrderNotes(group.orders[0]?.notes)
  }))
}
