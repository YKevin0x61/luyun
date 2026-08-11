/**
 * 菜品分组聚合工具（共享）
 *
 * 抽取自原 stores/dishes.js（仪表盘用，按 菜品+档口 分组、跨全部状态）与
 * pages/kitchen/kitchen.vue（厨房页用，按 菜品 分组、仅当前档口待出餐订单）中
 * 重复实现的"按分组键聚合订单、累加数量、收集订单列表"逻辑。
 *
 * 本工具只负责分组这一共同步骤；等待时长/紧急数/最早下单时间等派生字段由
 * 各消费方按自身口径在分组结果之上继续计算，口径不因此改变。
 */

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
