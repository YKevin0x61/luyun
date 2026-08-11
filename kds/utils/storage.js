/**
 * 本地存储管理工具
 * 用于保存和获取应用配置
 */

const STORAGE_KEYS = {
  API_SETTINGS: 'kds_api_settings',
  API_AUTH: 'kds_api_auth',
  USER_PREFERENCES: 'kds_user_preferences',
  PRINTER_SETTINGS: 'kds_printer_settings',
  PRINT_QUEUE: 'kds_print_queue',
  SCREEN_SETTINGS: 'kds_screen_settings'
}

/** 厨房页显示密度（对应 CSS class `density-<mode>`） */
export const DENSITY_MODES = Object.freeze({
  STANDARD: 'standard',
  COMPACT: 'compact',
  ULTRA: 'ultra'
})

const DENSITY_MODE_SET = new Set(Object.values(DENSITY_MODES))

const DEFAULT_ALERT_PARAMS = Object.freeze({
  beepCap: 5,
  reescalateSec: 20,
  badgeDismissSec: 30,
  warnMin: 15,
  urgentMin: 20,
  overtimeRepeatSec: 30
})

function normalizeWatchedStations(value) {
  if (!Array.isArray(value)) return []
  const seen = new Set()
  const result = []
  for (const item of value) {
    if (typeof item !== 'string') continue
    const id = item.trim()
    if (!id || seen.has(id)) continue
    seen.add(id)
    result.push(id)
  }
  return result
}

function normalizeDensity(value) {
  return DENSITY_MODE_SET.has(value) ? value : DENSITY_MODES.STANDARD
}

function normalizeAlertNumber(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) && n >= 0 ? n : fallback
}

function normalizeAlertParams(value) {
  const raw = value && typeof value === 'object' ? value : {}
  return {
    beepCap: normalizeAlertNumber(raw.beepCap, DEFAULT_ALERT_PARAMS.beepCap),
    reescalateSec: normalizeAlertNumber(raw.reescalateSec, DEFAULT_ALERT_PARAMS.reescalateSec),
    badgeDismissSec: normalizeAlertNumber(raw.badgeDismissSec, DEFAULT_ALERT_PARAMS.badgeDismissSec),
    warnMin: normalizeAlertNumber(raw.warnMin, DEFAULT_ALERT_PARAMS.warnMin),
    urgentMin: normalizeAlertNumber(raw.urgentMin, DEFAULT_ALERT_PARAMS.urgentMin),
    overtimeRepeatSec: normalizeAlertNumber(
      raw.overtimeRepeatSec,
      DEFAULT_ALERT_PARAMS.overtimeRepeatSec
    )
  }
}

/**
 * 规范化 API 基础地址（补全 http://，去掉末尾斜杠）
 * @param {string} baseUrl
 * @returns {string}
 */
function normalizeBaseUrl(baseUrl) {
  if (!baseUrl || typeof baseUrl !== 'string') {
    return ''
  }
  let url = baseUrl.trim().replace(/\/$/, '')
  if (!url) {
    return ''
  }
  if (!/^https?:\/\//i.test(url)) {
    url = `http://${url}`
  }
  return url
}

/**
 * API 设置管理
 */
export class ApiSettingsManager {
  /**
   * 获取 API 设置
   * @returns {Object} API 设置对象
   */
  static getApiSettings() {
    try {
      const settings = uni.getStorageSync(STORAGE_KEYS.API_SETTINGS)
      return settings ? JSON.parse(settings) : null
    } catch (error) {
      console.error('获取 API 设置失败:', error)
      return null
    }
  }

  /**
   * 保存 API 设置
   * @param {Object} settings API 设置对象
   */
  static saveApiSettings(settings) {
    try {
      uni.setStorageSync(STORAGE_KEYS.API_SETTINGS, JSON.stringify(settings))
      return true
    } catch (error) {
      console.error('保存 API 设置失败:', error)
      return false
    }
  }

  /**
   * 获取当前 API 基础地址
   * @returns {string} API 基础地址
   */
  static getBaseUrl() {
    const settings = this.getApiSettings()
    if (settings && settings.baseUrl) {
      return normalizeBaseUrl(settings.baseUrl)
    }
    
    // 返回默认配置
    return process.env.NODE_ENV === 'development'
      ? 'http://localhost:8000'
      : 'https://luyun.ykevin0x61.com'
  }

  /**
   * 设置 API 基础地址
   * @param {string} baseUrl API 基础地址
   */
  static setBaseUrl(baseUrl) {
    const currentSettings = this.getApiSettings() || {}
    const newSettings = {
      ...currentSettings,
      baseUrl: normalizeBaseUrl(baseUrl),
      updatedAt: new Date().toISOString()
    }
    return this.saveApiSettings(newSettings)
  }

  /**
   * 重置为默认设置
   */
  static resetToDefault() {
    try {
      uni.removeStorageSync(STORAGE_KEYS.API_SETTINGS)
      return true
    } catch (error) {
      console.error('重置设置失败:', error)
      return false
    }
  }

  /**
   * 测试 API 连接
   * @param {string} baseUrl 要测试的 API 地址
   * @returns {Promise<boolean>} 连接测试结果
   */
  static async testConnection(baseUrl) {
    try {
      const normalized = normalizeBaseUrl(baseUrl)
      if (!normalized) {
        return false
      }
      const testUrl = `${normalized}/api/system/health`
      
      const response = await uni.request({
        url: testUrl,
        method: 'GET',
        timeout: 5000
      })

      return response.statusCode === 200
    } catch (error) {
      console.error('API 连接测试失败:', error)
      return false
    }
  }
}

/**
 * API 鉴权 Token 管理
 */
export class ApiAuthManager {
  static getAuth() {
    try {
      const raw = uni.getStorageSync(STORAGE_KEYS.API_AUTH)
      return raw ? JSON.parse(raw) : null
    } catch (error) {
      console.error('获取 API 鉴权失败:', error)
      return null
    }
  }

  static getToken() {
    return this.getAuth()?.token || ''
  }

  static saveToken(token, mode = 'manual', extra = {}) {
    try {
      const payload = {
        token: (token || '').trim(),
        mode,
        ...extra,
        updatedAt: new Date().toISOString()
      }
      uni.setStorageSync(STORAGE_KEYS.API_AUTH, JSON.stringify(payload))
      return true
    } catch (error) {
      console.error('保存 API Token 失败:', error)
      return false
    }
  }

  static clearAuth() {
    try {
      uni.removeStorageSync(STORAGE_KEYS.API_AUTH)
      return true
    } catch (error) {
      console.error('清除 API 鉴权失败:', error)
      return false
    }
  }
}

/**
 * 蓝牙打印机设置管理
 */
export class PrinterSettingsManager {
  static getDefaultSettings() {
    return {
      enabled: false,
      deviceAddress: '',
      deviceName: '',
      updatedAt: null
    }
  }

  static getPrinterSettings() {
    try {
      const raw = uni.getStorageSync(STORAGE_KEYS.PRINTER_SETTINGS)
      if (raw) {
        return { ...this.getDefaultSettings(), ...JSON.parse(raw) }
      }
    } catch (error) {
      console.error('获取打印机设置失败:', error)
    }
    return this.getDefaultSettings()
  }

  static savePrinterSettings(settings) {
    try {
      const payload = {
        ...this.getDefaultSettings(),
        ...settings,
        updatedAt: new Date().toISOString()
      }
      uni.setStorageSync(STORAGE_KEYS.PRINTER_SETTINGS, JSON.stringify(payload))
      return true
    } catch (error) {
      console.error('保存打印机设置失败:', error)
      return false
    }
  }

  static isPrintEnabled() {
    return !!this.getPrinterSettings().enabled
  }

  static setPrintEnabled(enabled) {
    const current = this.getPrinterSettings()
    return this.savePrinterSettings({ ...current, enabled: !!enabled })
  }

  static savePrinterDevice(device) {
    const current = this.getPrinterSettings()
    return this.savePrinterSettings({
      ...current,
      deviceAddress: device.address || '',
      deviceName: device.name || ''
    })
  }

  static resetToDefault() {
    try {
      uni.removeStorageSync(STORAGE_KEYS.PRINTER_SETTINGS)
      return true
    } catch (error) {
      console.error('重置打印机设置失败:', error)
      return false
    }
  }
}

/**
 * 打印任务队列持久化
 * 仅负责队列数据的存取（业务逻辑——入队/重试/退避——在 utils/printQueue.js），
 * 用于进程重启后尽力恢复未完成/失败的打印任务，供厨房页展示与手动补打。
 */
export class PrintQueueManager {
  /**
   * 读取持久化的打印任务列表
   * @returns {Array} 任务数组，读取失败或为空时返回 []
   */
  static getQueue() {
    try {
      const raw = uni.getStorageSync(STORAGE_KEYS.PRINT_QUEUE)
      if (!raw) return []
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed : []
    } catch (error) {
      console.error('读取打印任务队列失败:', error)
      return []
    }
  }

  /**
   * 保存打印任务列表（整体覆盖）
   * @param {Array} jobs 任务数组
   */
  static saveQueue(jobs) {
    try {
      uni.setStorageSync(STORAGE_KEYS.PRINT_QUEUE, JSON.stringify(Array.isArray(jobs) ? jobs : []))
      return true
    } catch (error) {
      console.error('保存打印任务队列失败:', error)
      return false
    }
  }

  static clearQueue() {
    try {
      uni.removeStorageSync(STORAGE_KEYS.PRINT_QUEUE)
      return true
    } catch (error) {
      console.error('清空打印任务队列失败:', error)
      return false
    }
  }
}

/**
 * 本屏 KDS 设备本地配置（职责档口集 / 密度 / 告警参数）。
 * 遵循 ADR 0002：按设备本地存储，不写后端。空职责档口集 = 全部档口。
 */
export class ScreenSettingsManager {
  static getDefaultSettings() {
    return {
      watchedStations: [],
      density: DENSITY_MODES.STANDARD,
      alert: { ...DEFAULT_ALERT_PARAMS }
    }
  }

  static getSettings() {
    try {
      const raw = uni.getStorageSync(STORAGE_KEYS.SCREEN_SETTINGS)
      if (!raw) return this.getDefaultSettings()
      const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
      if (!parsed || typeof parsed !== 'object') return this.getDefaultSettings()
      return {
        watchedStations: normalizeWatchedStations(parsed.watchedStations),
        density: normalizeDensity(parsed.density),
        alert: normalizeAlertParams(parsed.alert)
      }
    } catch (error) {
      console.error('获取本屏 KDS 设置失败:', error)
      return this.getDefaultSettings()
    }
  }

  static saveSettings(settings) {
    try {
      const current = this.getSettings()
      const payload = {
        watchedStations: normalizeWatchedStations(
          settings?.watchedStations !== undefined
            ? settings.watchedStations
            : current.watchedStations
        ),
        density: normalizeDensity(
          settings?.density !== undefined ? settings.density : current.density
        ),
        alert: normalizeAlertParams(
          settings?.alert !== undefined
            ? { ...current.alert, ...settings.alert }
            : current.alert
        ),
        updatedAt: new Date().toISOString()
      }
      uni.setStorageSync(STORAGE_KEYS.SCREEN_SETTINGS, JSON.stringify(payload))
      return true
    } catch (error) {
      console.error('保存本屏 KDS 设置失败:', error)
      return false
    }
  }

  /** @returns {string[]} 空数组表示全部档口 */
  static getWatchedStations() {
    return this.getSettings().watchedStations
  }

  static setWatchedStations(stationIds) {
    return this.saveSettings({ watchedStations: stationIds })
  }

  /**
   * 某档口是否属于本屏职责。空集合语义为全部档口。
   * @param {string} stationId
   */
  static isStationWatched(stationId) {
    if (typeof stationId !== 'string' || !stationId.trim()) return false
    const watched = this.getWatchedStations()
    if (watched.length === 0) return true
    return watched.includes(stationId.trim())
  }

  static getDensity() {
    return this.getSettings().density
  }

  static setDensity(mode) {
    return this.saveSettings({ density: mode })
  }

  static getAlertParams() {
    return this.getSettings().alert
  }

  static setAlertParams(partial) {
    return this.saveSettings({ alert: partial || {} })
  }

  static resetToDefault() {
    try {
      uni.removeStorageSync(STORAGE_KEYS.SCREEN_SETTINGS)
      return true
    } catch (error) {
      console.error('重置本屏 KDS 设置失败:', error)
      return false
    }
  }
}

export default {
  ApiSettingsManager,
  ApiAuthManager,
  PrinterSettingsManager,
  PrintQueueManager,
  ScreenSettingsManager,
  DENSITY_MODES
} 
