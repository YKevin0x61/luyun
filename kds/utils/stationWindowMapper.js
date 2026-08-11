/**
 * 档口到窗口映射工具  
 * 根据菜品档口自动分配地哩部窗口（无桌号区域划分）
 */

export class StationWindowMapper {
  // 档口到窗口的直接映射规则
  static STATION_WINDOW_MAPPING = {
    'xibing': 1,      // 西饼档 → 窗口1
    'changfen': 2,    // 肠粉档 → 窗口2  
    'shulong': 2,     // 熟笼档 → 窗口2
    'mingdang1': 4,   // 明档1 → 窗口4
    'mingdang2': 3,   // 明档2 → 窗口3
    'jianzha': 4,     // 煎炸档 → 窗口4
    'qita': 5         // 其他档口 → 其他窗口5
  }

  // 窗口配置信息
  static WINDOW_CONFIG = {
    1: { name: '窗口1', description: '西饼档专用窗口', stations: ['xibing'] },
    2: { name: '窗口2', description: '肠粉蒸笼共用窗口', stations: ['changfen', 'shulong'] },
    3: { name: '窗口3', description: '明档2专用窗口', stations: ['mingdang2'] },
    4: { name: '窗口4', description: '明档1煎炸共用窗口', stations: ['mingdang1', 'jianzha'] },
    5: { name: '其他窗口', description: '其他档口专用窗口', stations: ['qita'] }
  }

  /**
   * 根据档口ID获取窗口ID
   * @param {String} stationId 档口ID
   * @returns {Number} 窗口ID
   */
  static getWindowByStation(stationId) {
    const windowId = this.STATION_WINDOW_MAPPING[stationId]
    
    if (windowId) {
      return windowId
    }

    // 默认分配到其他窗口5
    console.warn(`未找到档口 ${stationId} 的窗口映射，默认分配到其他窗口5`)
    return 5
  }

  /**
   * 根据窗口ID获取负责的档口列表
   * @param {Number} windowId 窗口ID
   * @returns {Array} 档口ID列表
   */
  static getStationsByWindow(windowId) {
    const stations = []
    
    for (const [stationId, mappedWindowId] of Object.entries(this.STATION_WINDOW_MAPPING)) {
      if (mappedWindowId === windowId) {
        stations.push(stationId)
      }
    }

    return stations
  }

  /**
   * 获取所有窗口的统计信息
   * @param {Array} orders 订单列表
   * @returns {Object} 窗口统计
   */
  static getWindowStats(orders) {
    const stats = {}
    
    // 初始化所有窗口的统计
    Object.keys(this.WINDOW_CONFIG).forEach(windowId => {
      const config = this.WINDOW_CONFIG[windowId]
      stats[`window${windowId}`] = {
        id: parseInt(windowId),
        name: config.name,
        description: config.description,
        stations: config.stations,
        orderCount: 0,
        pendingCount: 0,
        dishCount: 0
      }
    })

    // 统计订单数据
    orders.forEach(order => {
      const windowId = this.getWindowByStation(order.station)
      const windowKey = `window${windowId}`
      
      if (stats[windowKey]) {
        stats[windowKey].orderCount++
        if (order.dish_status === '已制作待上菜') {
          stats[windowKey].pendingCount++
        }
        stats[windowKey].dishCount += order.quantity
      }
    })

    return stats
  }

  /**
   * 获取窗口配置信息
   * @param {Number} windowId 窗口ID
   * @returns {Object} 窗口配置
   */
  static getWindowConfig(windowId) {
    return this.WINDOW_CONFIG[windowId] || {
      name: '未知窗口',
      description: '未配置的窗口',
      stations: []
    }
  }

  /**
   * 验证档口ID格式
   * @param {String} stationId 档口ID
   * @returns {Boolean} 是否有效
   */
  static isValidStationId(stationId) {
    return stationId && this.STATION_WINDOW_MAPPING.hasOwnProperty(stationId)
  }

  /**
   * 获取支持的所有档口列表
   * @returns {Array} 档口ID列表
   */
  static getAllStations() {
    return Object.keys(this.STATION_WINDOW_MAPPING)
  }

  /**
   * 获取支持的所有窗口列表
   * @returns {Array} 窗口配置列表
   */
  static getAllWindows() {
    return Object.entries(this.WINDOW_CONFIG).map(([id, config]) => ({
      id: parseInt(id),
      ...config
    }))
  }

  /**
   * 获取窗口负载统计
   * @param {Array} orders 订单列表
   * @returns {Object} 负载统计
   */
  static getWindowLoadStats(orders) {
    const stats = this.getWindowStats(orders)
    const loadStats = {}

    Object.entries(stats).forEach(([windowKey, windowData]) => {
      const loadLevel = this.calculateLoadLevel(windowData.pendingCount)
      loadStats[windowKey] = {
        ...windowData,
        loadLevel,
        loadDescription: this.getLoadDescription(loadLevel)
      }
    })

    return loadStats
  }

  /**
   * 计算窗口负载等级
   * @param {Number} pendingCount 待上菜数量
   * @returns {String} 负载等级
   */
  static calculateLoadLevel(pendingCount) {
    if (pendingCount >= 10) return 'high'
    if (pendingCount >= 5) return 'medium'
    return 'low'
  }

  /**
   * 获取负载描述
   * @param {String} loadLevel 负载等级
   * @returns {String} 负载描述
   */
  static getLoadDescription(loadLevel) {
    const descriptions = {
      low: '负载较轻',
      medium: '负载适中',
      high: '负载较重'
    }
    return descriptions[loadLevel] || '未知负载'
  }

  /**
   * 获取窗口颜色主题
   * @param {Number} windowId 窗口ID
   * @returns {String} 颜色代码
   */
  static getWindowColor(windowId) {
    const colors = {
      1: '#FF6B6B', // 西饼档 - 红色
      2: '#4ECDC4', // 肠粉蒸笼 - 青色
      3: '#45B7D1', // 明档2 - 蓝色
      4: '#96CEB4', // 明档1煎炸 - 绿色
      5: '#FFEAA7'  // 其他档口 - 黄色
    }
    return colors[windowId] || '#95A5A6'
  }
}

// 导出默认实例
export default StationWindowMapper 