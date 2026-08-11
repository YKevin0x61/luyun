/**
 * 订单相关API接口
 */

import { request } from '../utils/request.js'

export const ordersAPI = {
  /**
   * 获取订单列表
   * @param {Object} params 查询参数
   * @returns {Promise} API响应
   */
  async getOrders(params = {}) {
    console.log('[订单API] 请求参数:', params)
    
    return await request({
      url: '/api/orders/',
      method: 'GET',
      params: {
        station: params.station,
        dish_status: params.dish_status ?? params.status,
        table_number: params.table_number,
        start_time: params.start_time,
        end_time: params.end_time,
        sort_by: params.sort_by || 'order_time',
        limit: params.limit || 1000,
        ...params
      }
    })
  },

  /**
   * 获取当天订单数据
   * @param {Object} params 查询参数
   * @returns {Promise} API响应
   */
  async getTodayOrders(params = {}) {
    const today = new Date()
    const startOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 0, 0, 0, 0)
    const endOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 23, 59, 59, 999)

    console.log(`[获取当天订单] 时间范围: ${startOfDay.toISOString()} 到 ${endOfDay.toISOString()}`)

    return await this.getOrders({
      start_time: startOfDay.toISOString(),
      end_time: endOfDay.toISOString(),
      ...params
    })
  },

  /**
   * 获取合并菜品数据
   * @param {Object} params 查询参数
   * @returns {Promise} API响应
   */
  async getMergedDishes(params = {}) {
    return await request({
      url: '/api/dishes/merged',
      method: 'GET',
      params: {
        station: params.station || 'all',
        sort_by: params.sort_by || 'time',
        date: params.date || new Date().toISOString().split('T')[0],
        ...params
      }
    })
  },

  /**
   * 厨房制作完成（标记为待上菜）
   * @param {Object} apiData 已构造好的API数据
   * @returns {Promise} API响应
   */
  async completeCooking(apiData) {
    return await request({
      url: '/api/orders/complete-cooking',
      method: 'POST',
      data: apiData
    })
  }
}
