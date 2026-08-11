#!/usr/bin/env javascript
// -*- coding: utf-8 -*-
/**
 * 爬虫控制API接口
 * 提供爬虫启动、停止和状态查询功能
 */

import { request } from '../utils/request.js'

/**
 * 爬虫管理API
 */
export const scraperAPI = {
  /**
   * 启动爬虫
   * @returns {Promise} 响应数据
   */
  async start() {
    return await request({
      url: '/api/scraper/start',
      method: 'POST'
    })
  },

  /**
   * 停止爬虫
   * @returns {Promise} 响应数据
   */
  async stop() {
    return await request({
      url: '/api/scraper/stop', 
      method: 'POST'
    })
  },

  /**
   * 获取爬虫状态
   * @returns {Promise} 响应数据
   */
  async getStatus() {
    return await request({
      url: '/api/scraper/status',
      method: 'GET'
    })
  }
}

export default scraperAPI 