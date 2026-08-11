/**
 * 菜品档口映射管理API
 * 提供dish_stations集合的前端接口封装
 */

import { request } from '../utils/request.js'

export const dishStationsAPI = {
  /**
   * 获取菜品档口映射列表
   * @param {Object} params - 查询参数
   * @param {boolean} params.all - 是否获取所有数据
   * @param {number} params.page - 页码
   * @param {number} params.page_size - 每页大小
   * @param {string} params.dish_name - 菜品名称筛选
   * @param {string} params.station_id - 档口ID筛选
   * @param {number} params.window_id - 窗口ID筛选
   * @returns {Promise} API响应
   */
  async getDishStations(params = {}) {
    return await request({
      url: '/api/dish-stations/',
      method: 'GET',
      params: {
        all: true,  // 始终获取所有数据
        ...params
      }
    })
  },

  /**
   * 获取统计信息
   * @returns {Promise} API响应
   */
  async getStats() {
    return await request({
      url: '/api/dish-stations/stats',
      method: 'GET'
    })
  },

  /**
   * 根据菜品名称获取映射
   * @param {string} dishName - 菜品名称
   * @returns {Promise} API响应
   */
  async getDishStation(dishName) {
    return await request({
      url: `/api/dish-stations/${encodeURIComponent(dishName)}`,
      method: 'GET'
    })
  },

  /**
   * 创建菜品档口映射
   * @param {Object} data - 映射数据
   * @param {string} data.dish_name - 菜品名称
   * @param {string} data.station_id - 档口ID
   * @param {number} data.window_id - 窗口ID
   * @param {string} data.notes - 备注
   * @returns {Promise} API响应
   */
  async createDishStation(data) {
    return await request({
      url: '/api/dish-stations/',
      method: 'POST',
      data
    })
  },

  /**
   * 更新菜品档口映射
   * @param {string} dishName - 菜品名称
   * @param {Object} data - 更新数据
   * @param {string} data.station_id - 档口ID
   * @param {number} data.window_id - 窗口ID
   * @param {string} data.notes - 备注
   * @returns {Promise} API响应
   */
  async updateDishStation(dishName, data) {
    return await request({
      url: `/api/dish-stations/${encodeURIComponent(dishName)}`,
      method: 'PUT',
      data
    })
  },

  /**
   * 删除菜品档口映射
   * @param {string} dishName - 菜品名称
   * @returns {Promise} API响应
   */
  async deleteDishStation(dishName) {
    return await request({
      url: `/api/dish-stations/${encodeURIComponent(dishName)}`,
      method: 'DELETE'
    })
  },

  /**
   * 批量创建菜品档口映射
   * @param {Array} mappings - 映射数组
   * @returns {Promise} API响应
   */
  async batchCreateDishStations(mappings) {
    return await request({
      url: '/api/dish-stations/batch',
      method: 'POST',
      data: { mappings }
    })
  },

  /**
   * 搜索菜品档口映射
   * @param {Object} searchData - 搜索条件
   * @param {string} searchData.dish_name - 菜品名称关键词
   * @param {string} searchData.station_id - 档口ID
   * @param {number} searchData.window_id - 窗口ID
   * @param {Object} pagination - 分页参数
   * @param {number} pagination.page - 页码
   * @param {number} pagination.page_size - 每页大小
   * @returns {Promise} API响应
   */
  async searchDishStations(searchData, pagination = {}) {
    return await request({
      url: `/api/dish-stations/search/?page=${pagination.page || 1}&page_size=${pagination.page_size || 20}`,
      method: 'POST',
      data: searchData
    })
  }
} 