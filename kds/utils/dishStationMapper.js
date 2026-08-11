/**
 * 菜品档口窗口映射工具
 * 基于dish_stations集合实现菜品到档口和窗口的自动分配
 */

import { STATION_WINDOW_MAPPING } from './constants.js'
import { get, post, put } from './request.js'

export class DishStationMapper {
  constructor() {
    this.dishStationMapping = new Map() // 菜品名称 → 档口和窗口映射
    this.stationWindowMapping = new Map() // 档口 → 默认窗口映射
    this.isLoaded = false
  }

  /**
   * 从数据库加载菜品映射配置
   * @returns {Promise<Boolean>} 加载是否成功
   */
  async loadDishStationsFromDB() {
    try {
      const response = await get('/api/dish-stations/', { all: true })

      if (response && (response.success !== false)) {
        const data = response.data || response
        if (Array.isArray(data)) {
          this.parseDishStations(data)
        } else if (data && Array.isArray(data.items)) {
          this.parseDishStations(data.items)
        }
        this.isLoaded = true
        console.log('菜品档口映射配置加载成功:', this.dishStationMapping.size, '个菜品')
        return true
      } else {
        throw new Error('获取菜品映射数据失败')
      }

    } catch (error) {
      console.error('加载菜品映射配置失败:', error)
      this.loadDefaultMapping()
      return false
    }
  }

  /**
   * 解析dish_stations数据
   * @param {Array} dishStations 数据库返回的菜品映射数据
   */
  parseDishStations(dishStations) {
    this.dishStationMapping.clear()
    this.stationWindowMapping.clear()

    // 解析菜品映射
    dishStations.forEach(item => {
      this.dishStationMapping.set(item.dish_name, {
        stationId: item.station_id,
        windowId: item.window_id,
        updatedAt: item.updated_at
      })

      // 统计档口的默认窗口
      if (!this.stationWindowMapping.has(item.station_id)) {
        this.stationWindowMapping.set(item.station_id, item.window_id)
      }
    })

    console.log('解析完成 - 菜品映射:', this.dishStationMapping.size, '档口默认窗口:', this.stationWindowMapping.size)
  }

  /**
   * 降级使用默认映射配置
   */
  loadDefaultMapping() {
    console.warn('使用默认菜品档口映射配置')
    
    // 默认档口窗口映射
    for (const [station, window] of Object.entries(STATION_WINDOW_MAPPING)) {
      this.stationWindowMapping.set(station, window)
    }

    this.isLoaded = true
  }

  /**
   * 根据菜品名称获取档口ID
   * @param {String} dishName 菜品名称
   * @returns {String} 档口ID
   */
  getStationByDish(dishName) {
    if (!this.isLoaded) {
      console.warn('菜品映射配置未加载')
      return 'qita'
    }

    const mapping = this.dishStationMapping.get(dishName)
    if (mapping) {
      return mapping.stationId
    }

    return 'qita'
  }

  /**
   * 根据菜品名称获取窗口ID
   * @param {String} dishName 菜品名称
   * @returns {Number} 窗口ID
   */
  getWindowByDish(dishName) {
    if (!this.isLoaded) {
      console.warn('菜品映射配置未加载')
      return STATION_WINDOW_MAPPING['qita'] || 5
    }

    const mapping = this.dishStationMapping.get(dishName)
    if (mapping) {
      return mapping.windowId
    }

    const stationId = this.getStationByDish(dishName)
    return this.stationWindowMapping.get(stationId) || STATION_WINDOW_MAPPING['qita'] || 5
  }

  /**
   * 根据档口ID获取默认窗口
   * @param {String} stationId 档口ID
   * @returns {Number} 窗口ID
   */
  getWindowByStation(stationId) {
    return this.stationWindowMapping.get(stationId) || 1
  }

  /**
   * 获取档口的所有菜品
   * @param {String} stationId 档口ID
   * @returns {Array} 菜品名称列表
   */
  getDishesByStation(stationId) {
    const dishes = []
    for (const [dishName, mapping] of this.dishStationMapping) {
      if (mapping.stationId === stationId) {
        dishes.push(dishName)
      }
    }
    return dishes
  }

  /**
   * 获取窗口的所有菜品
   * @param {Number} windowId 窗口ID
   * @returns {Array} 菜品名称列表
   */
  getDishesByWindow(windowId) {
    const dishes = []
    for (const [dishName, mapping] of this.dishStationMapping) {
      if (mapping.windowId === windowId) {
        dishes.push(dishName)
      }
    }
    return dishes
  }

  /**
   * 添加或更新菜品映射
   * @param {String} dishName 菜品名称
   * @param {String} stationId 档口ID
   * @param {Number} windowId 窗口ID
   */
  setDishMapping(dishName, stationId, windowId) {
    this.dishStationMapping.set(dishName, {
      stationId,
      windowId,
      updatedAt: new Date().toISOString()
    })
    
    // 同步更新到数据库
    this.updateDishMappingToDB(dishName, stationId, windowId)
  }

  /**
   * 同步更新映射到数据库
   * @param {String} dishName 菜品名称
   * @param {String} stationId 档口ID
   * @param {Number} windowId 窗口ID
   */
  async updateDishMappingToDB(dishName, stationId, windowId) {
    try {
      await put(`/api/dish-stations/${encodeURIComponent(dishName)}`, {
        station_id: stationId,
        window_id: windowId
      })
      console.log(`更新菜品映射: ${dishName} → ${stationId} → 窗口${windowId}`)
    } catch (updateError) {
      try {
        await post('/api/dish-stations/', {
          dish_name: dishName,
          station_id: stationId,
          window_id: windowId
        })
        console.log(`创建菜品映射: ${dishName} → ${stationId} → 窗口${windowId}`)
      } catch (createError) {
        console.error('更新/创建菜品映射失败:', createError)
      }
    }
  }

  /**
   * 获取映射统计信息
   * @returns {Object} 统计信息
   */
  getStatistics() {
    const stationStats = {}
    const windowStats = {}

    for (const [dishName, mapping] of this.dishStationMapping) {
      // 档口统计
      if (!stationStats[mapping.stationId]) {
        stationStats[mapping.stationId] = 0
      }
      stationStats[mapping.stationId]++

      // 窗口统计
      if (!windowStats[mapping.windowId]) {
        windowStats[mapping.windowId] = 0
      }
      windowStats[mapping.windowId]++
    }

    return {
      totalDishes: this.dishStationMapping.size,
      stationStats,
      windowStats,
      isLoaded: this.isLoaded,
      loadTime: new Date().toISOString()
    }
  }

  /**
   * 重新加载映射配置
   */
  async reload() {
    this.isLoaded = false
    return await this.loadDishStationsFromDB()
  }

  /**
   * 验证菜品映射是否存在
   * @param {String} dishName 菜品名称
   * @returns {Boolean} 是否存在精确映射
   */
  hasDishMapping(dishName) {
    return this.dishStationMapping.has(dishName)
  }

  /**
   * 获取未分类菜品列表
   * @param {Array} allDishes 所有菜品列表
   * @returns {Array} 未分类菜品列表
   */
  getUnclassifiedDishes(allDishes) {
    return allDishes.filter(dishName => !this.hasDishMapping(dishName))
  }

  /**
   * 批量分类菜品
   * @param {Array} mappings 映射数组 [{dishName, stationId, windowId}]
   */
  async batchUpdateMappings(mappings) {
    try {
      mappings.forEach(mapping => {
        this.dishStationMapping.set(mapping.dishName, {
          stationId: mapping.stationId,
          windowId: mapping.windowId,
          updatedAt: new Date().toISOString()
        })
      })

      const snakeCaseMappings = mappings.map(m => ({
        dish_name: m.dishName,
        station_id: m.stationId,
        window_id: m.windowId
      }))
      await post('/api/dish-stations/batch', { mappings: snakeCaseMappings })

      console.log(`批量更新菜品映射: ${mappings.length} 个菜品`)
      return true

    } catch (error) {
      console.error('批量更新菜品映射失败:', error)
      return false
    }
  }
}

// 导出单例
export default new DishStationMapper() 