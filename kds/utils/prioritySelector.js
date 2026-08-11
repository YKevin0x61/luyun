/**
 * 订单优先级选择逻辑工具
 * 用于菜品出餐时自动选择最优订单组合
 */

import { TimeCalculator } from './timeCalculator.js'
import { getTimeThresholdsMs } from './timeThresholds.js'

export class OrderPrioritySelector {
  // 优先级权重配置
  static PRIORITY_WEIGHTS = {
    urgent: 10,
    high: 5,
    normal: 1
  }

  static get TIME_THRESHOLDS() {
    return getTimeThresholdsMs()
  }

  /**
   * 自动选择最优订单组合
   * @param {Array} orders 可选订单列表
   * @param {Number} targetQuantity 目标数量
   * @returns {Array} 选中的订单列表
   */
  static autoSelectOrders(orders, targetQuantity) {
    if (!orders || orders.length === 0 || targetQuantity <= 0) {
      console.warn('[优先级选择器] 无效参数:', { orders: orders?.length, targetQuantity })
      return []
    }

    // 🔍 调试：打印输入数据
    console.log('[优先级选择器] 输入订单数量:', orders.length)
    console.log('[优先级选择器] 目标数量:', targetQuantity)
    console.log('[优先级选择器] 订单样本:', orders[0])

    // 按优先级排序订单
    const sortedOrders = this.sortOrdersByPriority(orders)
    const selectedOrders = []
    let remainingQuantity = targetQuantity

    for (const order of sortedOrders) {
      if (remainingQuantity <= 0) break

      // 🔧 修复：处理不同的数量字段名，支持served_quantity
      const quantity = order.quantity || 1
      const servedQuantity = order.servedQuantity || order.served_quantity || 0
      const availableQuantity = quantity - servedQuantity
      
      console.log(`[优先级选择器] 订单${order._id || order.id}: 总量=${quantity}, 已出=${servedQuantity}, 可用=${availableQuantity}`)
      
      if (availableQuantity <= 0) {
        console.log(`[优先级选择器] 跳过订单${order._id || order.id}: 无可用数量`)
        continue
      }

      const selectQuantity = Math.min(availableQuantity, remainingQuantity)
      
      // 🔧 修复：确保必要字段存在
      const orderId = order._id || order.id
      const tableNumber = order.table_number
      
      if (!orderId || !tableNumber) {
        console.warn(`[优先级选择器] 跳过订单: 缺少必要字段`, { orderId, tableNumber, order })
        continue
      }
      
      selectedOrders.push({
        orderId: orderId,
        businessFlowId: order.business_flow_id || '',
        tableNumber: tableNumber,
        originalQuantity: quantity,
        serveQuantity: selectQuantity,
        priority: this.calculatePriority(order.order_time),
        waitTime: TimeCalculator.calculateCookingDuration(order.order_time)
      })

      remainingQuantity -= selectQuantity
      console.log(`[优先级选择器] 选择订单${orderId}: 选择数量=${selectQuantity}, 剩余目标=${remainingQuantity}`)
    }

    console.log(`[优先级选择器] 最终选择了${selectedOrders.length}个订单`)
    return selectedOrders
  }

  /**
   * 按优先级排序订单
   * @param {Array} orders 订单列表
   * @returns {Array} 排序后的订单列表
   */
  static sortOrdersByPriority(orders) {
    return [...orders].sort((a, b) => {
      // 1. 按优先级排序
      const aPriority = this.calculatePriority(a.order_time)
      const bPriority = this.calculatePriority(b.order_time)
      const priorityDiff = this.PRIORITY_WEIGHTS[bPriority] - this.PRIORITY_WEIGHTS[aPriority]
      
      if (priorityDiff !== 0) return priorityDiff

      // 2. 按下单时间排序（早的优先）
      const aTime = new Date(a.order_time).getTime()
      const bTime = new Date(b.order_time).getTime()
      return aTime - bTime
    })
  }

  /**
   * 计算订单优先级
   * @param {String} orderTime 下单时间
   * @returns {String} 优先级等级
   */
  static calculatePriority(orderTime) {
    const waitTime = TimeCalculator.calculateCookingDuration(orderTime)
    
    if (waitTime >= this.TIME_THRESHOLDS.urgent) {
      return 'urgent'
    } else if (waitTime >= this.TIME_THRESHOLDS.warning) {
      return 'high'
    } else {
      return 'normal'
    }
  }

  /**
   * 获取优先级颜色
   * @param {String} priority 优先级
   * @returns {String} 颜色代码
   */
  static getPriorityColor(priority) {
    const colors = {
      urgent: '#FF4D4F',  // 红色
      high: '#FA8C16',    // 橙色
      normal: '#52C41A'   // 绿色
    }
    return colors[priority] || colors.normal
  }

  /**
   * 获取优先级标签
   * @param {String} priority 优先级
   * @returns {String} 优先级标签
   */
  static getPriorityLabel(priority) {
    const labels = {
      urgent: '紧急',
      high: '较急',
      normal: '正常'
    }
    return labels[priority] || labels.normal
  }

  /**
   * 获取订单完整优先级信息
   * @param {Object} order 订单对象
   * @returns {Object} 优先级信息
   */
  static getOrderPriorityInfo(order) {
    const priority = this.calculatePriority(order.order_time)
    const waitTime = TimeCalculator.calculateCookingDuration(order.order_time)
    
    return {
      priority,
      label: this.getPriorityLabel(priority),
      color: this.getPriorityColor(priority),
      waitTime,
      waitTimeFormatted: TimeCalculator.formatDuration(waitTime),
      isOvertime: waitTime >= this.TIME_THRESHOLDS.warning,
      isUrgent: waitTime >= this.TIME_THRESHOLDS.urgent
    }
  }

  /**
   * 验证选择的订单组合
   * @param {Array} selectedOrders 选中的订单
   * @param {Number} targetQuantity 目标数量
   * @returns {Object} 验证结果
   */
  static validateSelection(selectedOrders, targetQuantity) {
    const totalSelected = selectedOrders.reduce((sum, order) => sum + order.serveQuantity, 0)
    
    return {
      isValid: totalSelected === targetQuantity,
      totalSelected,
      targetQuantity,
      difference: totalSelected - targetQuantity,
      orders: selectedOrders.length
    }
  }

  /**
   * 智能推荐订单组合
   * @param {Array} orders 可选订单列表
   * @param {Number} targetQuantity 目标数量
   * @returns {Object} 推荐结果
   */
  static recommendSelection(orders, targetQuantity) {
    const autoSelected = this.autoSelectOrders(orders, targetQuantity)
    const validation = this.validateSelection(autoSelected, targetQuantity)
    
    // 分析推荐理由
    const reasons = []
    if (autoSelected.some(order => order.priority === 'urgent')) {
      reasons.push('包含紧急订单，建议优先处理')
    }
    if (autoSelected.some(order => order.priority === 'high')) {
      reasons.push('包含较急订单，建议及时处理')
    }
    if (autoSelected.length > 0) {
      const avgWaitTime = autoSelected.reduce((sum, order) => sum + order.waitTime, 0) / autoSelected.length
      if (avgWaitTime > this.TIME_THRESHOLDS.warning) {
        reasons.push(`平均等待时间${TimeCalculator.formatDuration(avgWaitTime)}，建议加快处理`)
      }
    }

    return {
      selectedOrders: autoSelected,
      validation,
      reasons,
      recommendation: this.generateRecommendationText(autoSelected, validation)
    }
  }

  /**
   * 生成推荐文本
   * @param {Array} selectedOrders 选中订单
   * @param {Object} validation 验证结果
   * @returns {String} 推荐文本
   */
  static generateRecommendationText(selectedOrders, validation) {
    if (!validation.isValid) {
      return `选择的订单数量(${validation.totalSelected})与目标数量(${validation.targetQuantity})不匹配`
    }

    if (selectedOrders.length === 0) {
      return '暂无可选订单'
    }

    const urgentCount = selectedOrders.filter(order => order.priority === 'urgent').length
    const highCount = selectedOrders.filter(order => order.priority === 'high').length
    
    let text = `推荐选择${selectedOrders.length}个订单`
    
    if (urgentCount > 0) {
      text += `，其中${urgentCount}个紧急订单`
    }
    if (highCount > 0) {
      text += `，${highCount}个较急订单`
    }
    
    return text
  }

  /**
   * 按优先级分组订单
   * @param {Array} orders 订单列表
   * @returns {Object} 分组结果
   */
  static groupOrdersByPriority(orders) {
    const groups = {
      urgent: [],
      high: [],
      normal: []
    }

    orders.forEach(order => {
      const priority = this.calculatePriority(order.order_time)
      if (groups[priority]) {
        groups[priority].push({
          ...order,
          priority,
          priorityInfo: this.getOrderPriorityInfo(order)
        })
      }
    })

    return groups
  }

  /**
   * 获取优先级统计
   * @param {Array} orders 订单列表
   * @returns {Object} 统计信息
   */
  static getPriorityStats(orders) {
    const groups = this.groupOrdersByPriority(orders)
    
    return {
      total: orders.length,
      urgent: groups.urgent.length,
      high: groups.high.length,
      normal: groups.normal.length,
      urgentRatio: orders.length > 0 ? (groups.urgent.length / orders.length * 100).toFixed(1) : 0,
      highRatio: orders.length > 0 ? (groups.high.length / orders.length * 100).toFixed(1) : 0,
      normalRatio: orders.length > 0 ? (groups.normal.length / orders.length * 100).toFixed(1) : 0
    }
  }

  /**
   * 检查是否需要优先处理
   * @param {Array} orders 订单列表
   * @returns {Object} 检查结果
   */
  static checkUrgentNeeded(orders) {
    const stats = this.getPriorityStats(orders)
    const urgentOrders = this.groupOrdersByPriority(orders).urgent
    
    return {
      hasUrgent: stats.urgent > 0,
      urgentCount: stats.urgent,
      urgentOrders,
      suggestion: stats.urgent > 0 ? `发现${stats.urgent}个紧急订单，建议优先处理` : '暂无紧急订单'
    }
  }
}

// 导出默认实例
export default OrderPrioritySelector 