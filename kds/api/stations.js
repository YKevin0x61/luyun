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
   * 服务端契约只有 start_time/end_time 两个可选窗口参数，没有 date（票 24），
   * 因此这里不再接受/透传日期参数。
   * @param {String} stationId 档口ID
   * @returns {Promise} API响应
   */
  async getStationStats(stationId) {
    return await request({
      url: `/api/orders/station/${stationId}/stats`,
      method: 'GET'
    })
  }
}

export default stationsAPI
