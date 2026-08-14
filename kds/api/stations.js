/**
 * 档口相关API接口
 */

import { request } from '../utils/request.js'

export const stationsAPI = {
  /**
   * Shop-level station catalog, including 熟笼 steamer_layout.
   * @returns {Promise<object[]>}
   */
  async getStations() {
    return await request({
      url: '/api/stations',
      method: 'GET'
    })
  },

  /**
   * 获取档口统计信息
   * @param {String} stationId 档口ID
   * @param {String} date 日期 (YYYY-MM-DD)
   * @returns {Promise} API响应
   */
  async getStationStats(stationId, date = null) {
    const params = {}
    if (date) {
      params.date = date
    }

    return await request({
      url: `/api/orders/station/${stationId}/stats`,
      method: 'GET',
      params
    })
  }
}

export default stationsAPI
