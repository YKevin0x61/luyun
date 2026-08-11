/**
 * 档口状态管理Store
 * 管理厨房档口的配置、统计和状态信息
 */

import { defineStore } from 'pinia'
import { stationsAPI } from '../api/stations.js'
import { StationWindowMapper } from '../utils/stationWindowMapper.js'
import { TimeCalculator } from '../utils/timeCalculator.js'

export const useStationsStore = defineStore('stations', {
  state: () => ({
    // 档口配置信息（单一数据源：厨房页 tabs 等消费方统一从这里读取，顺序即厨房页 tabs 的展示顺序）
    // 名称/id 与后端 config.py 的 KITCHEN_STATIONS 保持一致；color 与厨房页历史展示色保持一致，避免切换数据源后出现视觉跳变
    stations: {
      changfen: {
        id: 'changfen',
        name: '肠粉档',
        color: '#4ECDC4',
        description: '负责制作各类肠粉和米制品',
        windowId: 2,
        isActive: true
      },
      shulong: {
        id: 'shulong',
        name: '熟笼档',
        color: '#45B7D1',
        description: '负责制作蒸制类点心',
        windowId: 2,
        isActive: true
      },
      xibing: {
        id: 'xibing',
        name: '西饼档',
        color: '#FF6B6B',
        description: '负责制作各类西式点心和烘焙产品',
        windowId: 1,
        isActive: true
      },
      mingdang1: {
        id: 'mingdang1',
        name: '明档1',
        color: '#96CEB4',
        description: '负责制作现点现做菜品',
        windowId: 4,
        isActive: true
      },
      mingdang2: {
        id: 'mingdang2',
        name: '明档2',
        color: '#FECA57',
        description: '负责制作特色菜品',
        windowId: 3,
        isActive: true
      },
      jianzha: {
        id: 'jianzha',
        name: '煎炸档',
        color: '#DDA0DD',
        description: '负责制作煎炸类菜品',
        windowId: 4,
        isActive: true
      },
      qita: {
        id: 'qita',
        name: '其他档口',
        color: '#A8A8A8',
        description: '处理未分类菜品和新菜品',
        windowId: 5,
        isActive: true
      }
    },

    // 档口统计数据
    stationStats: {},

    // 当前选中的档口
    currentStation: 'all',

    // 加载状态
    loading: false,
    error: null,

    // 最后更新时间
    lastUpdated: null,

    // 档口实时状态
    stationStatus: {}
  }),

  getters: {
    // 获取所有激活的档口
    activeStations: (state) => {
      return Object.values(state.stations).filter(station => station.isActive)
    },

    // 获取档口列表
    stationList: (state) => {
      return Object.values(state.stations)
    },

    // 根据档口ID获取档口信息
    getStationById: (state) => (stationId) => {
      return state.stations[stationId] || null
    },

    // 根据窗口ID获取负责的档口列表
    getStationsByWindow: (state) => (windowId) => {
      return Object.values(state.stations).filter(
        station => station.windowId === windowId && station.isActive
      )
    },

    // 获取档口统计信息
    getStationStats: (state) => (stationId) => {
      return state.stationStats[stationId] || {
        orderCount: 0,
        pendingCount: 0,
        completedCount: 0,
        avgCookingTime: 0,
        efficiency: 0
      }
    },

    // 获取档口负载等级
    getStationLoadLevel: (state) => (stationId) => {
      const stats = state.stationStats[stationId]
      if (!stats) return 'low'

      const pendingCount = stats.pendingCount || 0
      if (pendingCount >= 15) return 'high'
      if (pendingCount >= 8) return 'medium'
      return 'low'
    },

    // 获取档口颜色主题
    getStationColor: (state) => (stationId) => {
      const station = state.stations[stationId]
      return station ? station.color : '#95A5A6'
    },

    // 档口总体统计
    overallStats: (state) => {
      const totalOrders = Object.values(state.stationStats).reduce(
        (sum, stats) => sum + (stats.orderCount || 0), 0
      )
      const totalPending = Object.values(state.stationStats).reduce(
        (sum, stats) => sum + (stats.pendingCount || 0), 0
      )
      const totalCompleted = Object.values(state.stationStats).reduce(
        (sum, stats) => sum + (stats.completedCount || 0), 0
      )

      return {
        totalOrders,
        totalPending,
        totalCompleted,
        completionRate: totalOrders > 0 ? ((totalCompleted / totalOrders) * 100).toFixed(1) : 0,
        activeStations: Object.values(state.stations).filter(s => s.isActive).length
      }
    },

    // 获取最繁忙的档口
    busiestStation: (state) => {
      let maxPending = 0
      let busiestStationId = null

      Object.entries(state.stationStats).forEach(([stationId, stats]) => {
        if (stats.pendingCount > maxPending) {
          maxPending = stats.pendingCount
          busiestStationId = stationId
        }
      })

      return busiestStationId ? {
        station: state.stations[busiestStationId],
        pendingCount: maxPending
      } : null
    },

    // 获取效率最高的档口
    mostEfficientStation: (state) => {
      let maxEfficiency = 0
      let efficientStationId = null

      Object.entries(state.stationStats).forEach(([stationId, stats]) => {
        if (stats.efficiency > maxEfficiency) {
          maxEfficiency = stats.efficiency
          efficientStationId = stationId
        }
      })

      return efficientStationId ? {
        station: state.stations[efficientStationId],
        efficiency: maxEfficiency
      } : null
    }
  },

  actions: {
    /**
     * 初始化档口配置
     */
    async initializeStations() {
      try {
        // 使用StationWindowMapper同步窗口映射
        const allStations = StationWindowMapper.getAllStations()
        
        allStations.forEach(stationId => {
          if (this.stations[stationId]) {
            this.stations[stationId].windowId = StationWindowMapper.getWindowByStation(stationId)
          }
        })

        console.log('档口配置初始化完成')
        return true
      } catch (error) {
        console.error('初始化档口配置失败:', error)
        this.error = error.message
        return false
      }
    },

    /**
     * 获取档口统计数据
     * @param {String} stationId 档口ID，不传则获取所有档口
     * @param {String} date 日期，默认当天
     */
    async fetchStationStats(stationId = null, date = null) {
      this.loading = true
      this.error = null

      try {
        if (stationId) {
          // 获取单个档口统计
          const response = await stationsAPI.getStationStats(stationId, date)
          if (response.success) {
            this.stationStats[stationId] = response.data
          }
        } else {
          // 获取所有档口统计
          const promises = Object.keys(this.stations).map(async (id) => {
            try {
              const response = await stationsAPI.getStationStats(id, date)
              if (response.success) {
                this.stationStats[id] = response.data
              }
            } catch (error) {
              console.warn(`获取档口${id}统计失败:`, error)
            }
          })

          await Promise.allSettled(promises)
        }

        this.lastUpdated = new Date()
      } catch (error) {
        console.error('获取档口统计失败:', error)
        this.error = error.message || '获取统计数据失败'
      } finally {
        this.loading = false
      }
    },

    /**
     * 更新档口统计数据（本地计算）
     * @param {Array} orders 订单列表
     */
    updateStationStatsFromOrders(orders) {
      const stats = {}

      // 初始化所有档口统计
      Object.keys(this.stations).forEach(stationId => {
        stats[stationId] = {
          orderCount: 0,
          pendingCount: 0,
          completedCount: 0,
          totalCookingTime: 0,
          avgCookingTime: 0,
          efficiency: 0
        }
      })

      // 计算统计数据
      orders.forEach(order => {
        const stationId = order.station
        if (!stats[stationId]) return

        stats[stationId].orderCount++

        switch (order.dish_status) {
          case '待出餐':
            stats[stationId].pendingCount++
            break
          case '已制作待上菜':
          case '已上菜':
            stats[stationId].completedCount++
            
            if (order.ready_time) {
              const cookingTime = TimeCalculator.calculateCookingDuration(
                order.order_time, 
                order.ready_time
              )
              stats[stationId].totalCookingTime += cookingTime
            }
            break
        }
      })

      // 计算平均制作时间和效率
      Object.keys(stats).forEach(stationId => {
        const stationStats = stats[stationId]
        
        if (stationStats.completedCount > 0) {
          stationStats.avgCookingTime = stationStats.totalCookingTime / stationStats.completedCount
          stationStats.efficiency = (stationStats.completedCount / stationStats.orderCount * 100).toFixed(1)
        }
      })

      this.stationStats = stats
    },

    /**
     * 设置当前档口
     * @param {String} stationId 档口ID
     */
    setCurrentStation(stationId) {
      this.currentStation = stationId
    },

    /**
     * 更新档口配置
     * @param {String} stationId 档口ID
     * @param {Object} config 配置信息
     */
    updateStationConfig(stationId, config) {
      if (this.stations[stationId]) {
        this.stations[stationId] = {
          ...this.stations[stationId],
          ...config
        }
      }
    },

    /**
     * 启用/禁用档口
     * @param {String} stationId 档口ID
     * @param {Boolean} isActive 是否启用
     */
    toggleStationStatus(stationId, isActive) {
      if (this.stations[stationId]) {
        this.stations[stationId].isActive = isActive
      }
    },

    /**
     * 获取档口详细信息
     * @param {String} stationId 档口ID
     * @returns {Object} 档口详细信息
     */
    getStationDetail(stationId) {
      const station = this.stations[stationId]
      if (!station) return null

      const stats = this.stationStats[stationId] || {}
      const loadLevel = this.getStationLoadLevel(stationId)
      const windowConfig = StationWindowMapper.getWindowConfig(station.windowId)

      return {
        ...station,
        stats,
        loadLevel,
        loadDescription: this.getLoadDescription(loadLevel),
        windowConfig,
        lastUpdated: this.lastUpdated
      }
    },

    /**
     * 获取负载描述
     * @param {String} loadLevel 负载等级
     * @returns {String} 负载描述
     */
    getLoadDescription(loadLevel) {
      const descriptions = {
        low: '负载较轻',
        medium: '负载适中',
        high: '负载较重'
      }
      return descriptions[loadLevel] || '未知负载'
    },

    /**
     * 实时更新档口状态
     * @param {String} stationId 档口ID
     * @param {Object} status 状态信息
     */
    updateStationStatus(stationId, status) {
      this.stationStatus[stationId] = {
        ...this.stationStatus[stationId],
        ...status,
        lastUpdate: new Date().toISOString()
      }
    },

    /**
     * 获取档口实时状态
     * @param {String} stationId 档口ID
     * @returns {Object} 实时状态
     */
    getStationStatus(stationId) {
      return this.stationStatus[stationId] || {
        isOnline: true,
        lastUpdate: null,
        currentOrders: 0,
        averageWaitTime: 0
      }
    },

    /**
     * 刷新档口数据
     */
    async refreshStationData() {
      await this.fetchStationStats()
    },

    /**
     * 重置档口状态
     */
    resetStationState() {
      this.stationStats = {}
      this.stationStatus = {}
      this.currentStation = 'all'
      this.loading = false
      this.error = null
      this.lastUpdated = null
    },

    /**
     * 获取档口配置列表
     * @param {Boolean} activeOnly 是否只返回激活的档口
     * @returns {Array} 档口配置列表
     */
    getStationConfigs(activeOnly = false) {
      const stations = Object.values(this.stations)
      return activeOnly ? stations.filter(station => station.isActive) : stations
    },

    /**
     * 根据名称搜索档口
     * @param {String} keyword 搜索关键词
     * @returns {Array} 匹配的档口列表
     */
    searchStations(keyword) {
      if (!keyword) return this.stationList

      const lowerKeyword = keyword.toLowerCase()
      return this.stationList.filter(station =>
        station.name.toLowerCase().includes(lowerKeyword) ||
        station.description.toLowerCase().includes(lowerKeyword)
      )
    }
  }
})

export default useStationsStore 