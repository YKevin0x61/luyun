/**
 * 菜品流转时间计算工具
 * 用于计算制作时长、上菜时长和总耗时
 */

import { TIME_THRESHOLDS } from './constants.js'

export class TimeCalculator {
  /**
   * 计算等待时间（从下单到现在的时长）
   * @param {String|Date} orderTime 下单时间
   * @returns {Number} 等待时间（毫秒）
   */
  static calculateWaitTime(orderTime) {
    const startTime = new Date(orderTime).getTime()
    const currentTime = Date.now()
    return Math.max(0, currentTime - startTime)
  }

  /**
   * 计算制作时长
   * @param {String} orderTime 下单时间
   * @param {String} readyTime 制作完成时间，为空则使用当前时间
   * @returns {Number} 制作时长（毫秒）
   */
  static calculateCookingDuration(orderTime, readyTime = null) {
    const startTime = new Date(orderTime).getTime()
    const endTime = readyTime ? new Date(readyTime).getTime() : Date.now()
    return Math.max(0, endTime - startTime)
  }

  /**
   * 计算上菜时长
   * @param {String} readyTime 制作完成时间
   * @param {String} servedTime 上菜完成时间，为空则使用当前时间
   * @returns {Number} 上菜时长（毫秒）
   */
  static calculateServingDuration(readyTime, servedTime = null) {
    const startTime = new Date(readyTime).getTime()
    const endTime = servedTime ? new Date(servedTime).getTime() : Date.now()
    return Math.max(0, endTime - startTime)
  }

  /**
   * 计算总耗时
   * @param {String} orderTime 下单时间
   * @param {String} servedTime 上菜完成时间，为空则使用当前时间
   * @returns {Number} 总耗时（毫秒）
   */
  static calculateTotalDuration(orderTime, servedTime = null) {
    const startTime = new Date(orderTime).getTime()
    const endTime = servedTime ? new Date(servedTime).getTime() : Date.now()
    return Math.max(0, endTime - startTime)
  }

  /**
   * 格式化时长显示
   * @param {Number} duration 时长（毫秒）
   * @param {String} format 格式类型 short/long
   * @returns {String} 格式化的时长字符串
   */
  static formatDuration(duration, format = 'short') {
    const safeMs = Math.max(0, Number(duration) || 0)
    const seconds = Math.floor(safeMs / 1000)
    const minutes = Math.floor(seconds / 60)
    const hours = Math.floor(minutes / 60)

    const remainingMinutes = minutes % 60
    const remainingSeconds = seconds % 60

    if (format === 'long') {
      if (hours > 0) {
        return `${hours}小时${remainingMinutes}分钟${remainingSeconds}秒`
      } else if (minutes > 0) {
        return `${remainingMinutes}分钟${remainingSeconds}秒`
      } else {
        return `${remainingSeconds}秒`
      }
    } else {
      // short format
      if (hours > 0) {
        return `${hours}:${String(remainingMinutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
      } else {
        return `${minutes}:${String(remainingSeconds).padStart(2, '0')}`
      }
    }
  }

  /**
   * 格式化为时钟样式 HH:MM:SS（用于厨房最长等待等）
   * @param {Number} duration 时长（毫秒）
   * @returns {String} 例如 00:15:30
   */
  static formatDurationClock(duration) {
    const safeMs = Math.max(0, Number(duration) || 0)
    const totalSeconds = Math.floor(safeMs / 1000)
    const hours = Math.floor(totalSeconds / 3600)
    const minutes = Math.floor((totalSeconds % 3600) / 60)
    const seconds = totalSeconds % 60
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }

  /**
   * 判断是否超时
   * @param {Number} duration 时长（毫秒）
   * @param {Number} threshold 阈值（毫秒）
   * @returns {Boolean} 是否超时
   */
  static isOvertime(duration, threshold) {
    return duration > threshold
  }

  /**
   * 获取超时等级
   * @param {Number} duration 时长（毫秒）
   * @param {Object} thresholds 阈值配置
   * @returns {String} 等级 normal/warning/urgent
   */
  static getOvertimeLevel(duration, thresholds = {}) {
    const {
      warning = TIME_THRESHOLDS.WARNING,
      urgent = TIME_THRESHOLDS.URGENT
    } = thresholds

    if (duration >= urgent) {
      return 'urgent'
    } else if (duration >= warning) {
      return 'warning'
    } else {
      return 'normal'
    }
  }

  /**
   * 计算平均时长
   * @param {Array} durations 时长数组
   * @returns {Number} 平均时长
   */
  static calculateAverageDuration(durations) {
    if (durations.length === 0) return 0
    const total = durations.reduce((sum, duration) => sum + duration, 0)
    return Math.round(total / durations.length)
  }

  /**
   * 实时更新时长
   * @param {String} startTime 开始时间
   * @param {Function} callback 回调函数
   * @param {Number} interval 更新间隔（毫秒）
   * @returns {Number} 定时器ID
   */
  static startRealTimeUpdate(startTime, callback, interval = 1000) {
    const updateFunction = () => {
      const duration = this.calculateCookingDuration(startTime)
      callback(duration)
    }

    // 立即执行一次
    updateFunction()

    // 开始定时更新
    return setInterval(updateFunction, interval)
  }

  /**
   * 停止实时更新
   * @param {Number} timerId 定时器ID
   */
  static stopRealTimeUpdate(timerId) {
    if (timerId) {
      clearInterval(timerId)
    }
  }

  /**
   * 格式化时间显示
   * @param {String|Date} time 时间
   * @param {String} format 格式类型
   * @returns {String} 格式化时间
   */
  static formatTime(time, format = 'HH:mm:ss') {
    const date = new Date(time)
    
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hours = String(date.getHours()).padStart(2, '0')
    const minutes = String(date.getMinutes()).padStart(2, '0')
    const seconds = String(date.getSeconds()).padStart(2, '0')

    switch (format) {
      case 'HH:mm:ss':
        return `${hours}:${minutes}:${seconds}`
      case 'HH:mm':
        return `${hours}:${minutes}`
      case 'YYYY-MM-DD':
        return `${year}-${month}-${day}`
      case 'YYYY-MM-DD HH:mm:ss':
        return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
      case 'MM-DD HH:mm':
        return `${month}-${day} ${hours}:${minutes}`
      default:
        return date.toLocaleString('zh-CN')
    }
  }

  /**
   * 获取相对时间描述
   * @param {String|Date} time 时间
   * @returns {String} 相对时间描述
   */
  static getRelativeTime(time) {
    const now = Date.now()
    const targetTime = new Date(time).getTime()
    const diff = now - targetTime
    
    if (diff < 60000) { // 1分钟内
      return '刚刚'
    } else if (diff < 3600000) { // 1小时内
      return `${Math.floor(diff / 60000)}分钟前`
    } else if (diff < 86400000) { // 24小时内
      return `${Math.floor(diff / 3600000)}小时前`
    } else {
      return this.formatTime(time, 'MM-DD HH:mm')
    }
  }

  /**
   * 格式化平均制作时长（分钟）
   * @param {Number} minutes 分钟数
   * @returns {String}
   */
  static formatAvgCookingMinutes(minutes) {
    const safeMinutes = Number(minutes) || 0
    if (safeMinutes <= 0) return '0分'
    if (safeMinutes >= 60) {
      const hours = Math.floor(safeMinutes / 60)
      const mins = Math.round(safeMinutes % 60)
      return mins > 0 ? `${hours}小时${mins}分` : `${hours}小时`
    }
    return `${Math.round(safeMinutes * 10) / 10}分`
  }

  /**
   * 检查是否是今天
   * @param {String|Date} time 时间
   * @returns {Boolean} 是否是今天
   */
  static isToday(time) {
    const today = new Date()
    const target = new Date(time)
    
    return today.getFullYear() === target.getFullYear() &&
           today.getMonth() === target.getMonth() &&
           today.getDate() === target.getDate()
  }

  /**
   * 获取今天的开始和结束时间
   * @returns {Object} 今天的时间范围
   */
  static getTodayRange() {
    const today = new Date()
    const startOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate())
    const endOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 23, 59, 59, 999)
    
    return {
      start: startOfDay.toISOString(),
      end: endOfDay.toISOString()
    }
  }
} 