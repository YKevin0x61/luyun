/**
 * HTTP请求封装工具
 * 基于uniapp的uni.request实现
 */

import { API_CONFIG } from './constants.js'
import { ApiAuthManager, ApiSettingsManager } from './storage.js'
import { debugLog } from './debug.js'

/**
 * 统一请求拦截器
 */
class RequestInterceptor {
  constructor() {
    this.timeout = API_CONFIG.TIMEOUT
  }

  /**
   * 获取当前的 API 基础地址
   * 优先使用本地存储的设置，否则使用默认配置
   */
  getBaseURL() {
    return ApiSettingsManager.getBaseUrl()
  }

  /**
   * 请求拦截器
   */
  requestInterceptor(config) {
    // 添加时间戳防止缓存
    if (config.params) {
      config.params._t = Date.now()
    } else {
      config.params = { _t: Date.now() }
    }

    // 添加请求头
    config.header = {
      'Content-Type': 'application/json',
      ...config.header
    }

    const token = ApiAuthManager.getToken()
    if (token) {
      config.header['X-Admin-Token'] = token
    }

    // 完整URL - 使用动态获取的 baseURL
    if (config.url && !config.url.startsWith('http')) {
      config.url = this.getBaseURL() + config.url
    }

    debugLog(`[请求] ${config.method} ${config.url}`, config.data || config.params)
    return config
  }

  /**
   * 响应拦截器
   */
  responseInterceptor(response, config) {
    const { statusCode, data } = response

    debugLog(`[响应] ${config.method} ${config.url}`, {
      statusCode,
      data
    })

    // HTTP状态码检查
    if (statusCode >= 200 && statusCode < 300) {
      return data
    } else {
      const error = new Error(`HTTP ${statusCode}: ${data?.message || data?.detail || '请求失败'}`)
      error.statusCode = statusCode
      error.response = response
      throw error
    }
  }

  /**
   * 错误处理
   */
  errorHandler(error, config) {
    console.error(`[请求错误] ${config.method} ${config.url}`, error)

    if (error.statusCode === 401) {
      const method = (config.method || 'GET').toUpperCase()
      if (['POST', 'PUT', 'DELETE'].includes(method)) {
        uni.showToast({
          title: '请先在设置中配置 API Token',
          icon: 'none'
        })
      }
    }

    // 网络错误
    if (error.errMsg && error.errMsg.includes('timeout')) {
      return Promise.reject(new Error('请求超时，请检查网络连接'))
    }

    if (error.errMsg && error.errMsg.includes('fail')) {
      return Promise.reject(new Error('网络连接失败，请检查网络设置'))
    }

    return Promise.reject(error)
  }
}

const interceptor = new RequestInterceptor()

/**
 * 统一请求方法
 * @param {Object} config 请求配置
 * @returns {Promise} 请求结果
 */
export function request(config) {
  return new Promise((resolve, reject) => {
    // 默认配置
    const defaultConfig = {
      method: 'GET',
      timeout: interceptor.timeout,
      dataType: 'json',
      responseType: 'text'
    }

    // 合并配置
    const finalConfig = { ...defaultConfig, ...config }

    // 请求拦截
    const interceptedConfig = interceptor.requestInterceptor(finalConfig)

    // 发起请求
    uni.request({
      ...interceptedConfig,
      success: (response) => {
        try {
          const result = interceptor.responseInterceptor(response, interceptedConfig)
          resolve(result)
        } catch (error) {
          interceptor.errorHandler(error, interceptedConfig).catch(reject)
        }
      },
      fail: (error) => {
        interceptor.errorHandler(error, interceptedConfig).catch(reject)
      }
    })
  })
}

/**
 * GET请求
 */
export function get(url, params = {}, config = {}) {
  return request({
    url,
    method: 'GET',
    params,
    ...config
  })
}

/**
 * POST请求
 */
export function post(url, data = {}, config = {}) {
  return request({
    url,
    method: 'POST',
    data,
    ...config
  })
}

/**
 * PUT请求
 */
export function put(url, data = {}, config = {}) {
  return request({
    url,
    method: 'PUT',
    data,
    ...config
  })
}

/**
 * DELETE请求
 */
export function del(url, params = {}, config = {}) {
  return request({
    url,
    method: 'DELETE',
    params,
    ...config
  })
}

export default {
  request,
  get,
  post,
  put,
  del
} 