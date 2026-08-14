/**
 * 订单状态管理Store
 * 管理订单的全生命周期状态变化
 */

import { defineStore } from 'pinia'
import { ordersAPI } from '../api/orders.js'
import { TimeCalculator } from '../utils/timeCalculator.js'
import { OrderPrioritySelector } from '../utils/prioritySelector.js'
import { groupOrdersByDish } from '../utils/dishMerge.js'
import { TIME_THRESHOLDS } from '../utils/constants.js'

export const useOrdersStore = defineStore('orders', {
  state: () => ({
    // 原始订单数据
    orders: [],
    
    // 当前选中的订单
    selectedOrders: [],
    
    // 订单筛选条件
    filters: {
      station: 'all',
      status: 'all',
      tableNumber: '',
      date: ''
    },
    
    // 排序方式
    sortBy: 'order_time',
    sortOrder: 'desc',
    
    // 加载状态
    loading: false,
    error: null,
    
    // 最后更新时间
    lastUpdated: null,
    
    // 数据版本号
    dataVersion: 0,
    
    // 操作状态
    operationLoading: false
  }),

  getters: {
    // 按状态过滤订单
    getOrdersByStatus: (state) => (status) => {
      if (status === 'all') return state.orders
      return state.orders.filter(order => order.dish_status === status)
    },

    // 按档口过滤订单
    getOrdersByStation: (state) => (stationId) => {
      // 🔧 防护措施：确保orders数组存在
      if (!Array.isArray(state.orders)) {
        console.warn('[OrdersStore] state.orders不是数组:', state.orders)
        return []
      }
      
      if (stationId === 'all') return state.orders
      
      // 🔧 防护措施：确保stationId存在
      if (!stationId) {
        console.warn('[OrdersStore] getOrdersByStation: stationId为空')
        return []
      }
      
      return state.orders.filter(order => order && order.station === stationId)
    },

    // 按桌号过滤订单
    getOrdersByTable: (state) => (tableNumber) => {
      if (!tableNumber) return state.orders
      return state.orders.filter(order => 
        order.table_number.toLowerCase().includes(tableNumber.toLowerCase())
      )
    },

    // 待出餐订单
    pendingOrders: (state) => {
      return state.orders.filter(order => order.dish_status === '待出餐')
    },

    // 已制作待上菜订单
    readyOrders: (state) => {
      return state.orders.filter(order => order.dish_status === '已制作待上菜')
    },

    // 已上菜订单
    servedOrders: (state) => {
      return state.orders.filter(order => order.dish_status === '已上菜')
    },

    // 紧急订单
    urgentOrders: (state) => {
      return state.orders.filter(order => {
        const priority = OrderPrioritySelector.calculatePriority(order.order_time)
        return priority === 'urgent'
      })
    },

    // 订单统计
    orderStats: (state) => {
      const total = state.orders.length
      const pending = state.orders.filter(order => order.dish_status === '待出餐').length
      const ready = state.orders.filter(order => order.dish_status === '已制作待上菜').length
      const served = state.orders.filter(order => order.dish_status === '已上菜').length

      return {
        total,
        pending,
        ready,
        served,
        completionRate: total > 0 ? ((served / total) * 100).toFixed(1) : 0
      }
    },

    // 应用筛选条件后的订单
    filteredOrders: (state) => {
      let filtered = [...state.orders]

      // 档口筛选
      if (state.filters.station !== 'all') {
        filtered = filtered.filter(order => order.station === state.filters.station)
      }

      // 状态筛选
      if (state.filters.status !== 'all') {
        filtered = filtered.filter(order => order.dish_status === state.filters.status)
      }

      // 桌号筛选
      if (state.filters.tableNumber) {
        filtered = filtered.filter(order => 
          order.table_number.toLowerCase().includes(state.filters.tableNumber.toLowerCase())
        )
      }

      // 日期筛选
      if (state.filters.date) {
        const filterDate = new Date(state.filters.date).toDateString()
        filtered = filtered.filter(order => 
          new Date(order.order_time).toDateString() === filterDate
        )
      }

      return filtered
    },

    // 今日全档口合并菜品（仪表盘用）：按 菜品+档口 分组，跨全部状态，供首页统计卡片复用
    // 合并自原 stores/dishes.js 的 mergeDishes()，算法与输出字段保持一致
    mergedDishes: (state) => {
      if (!Array.isArray(state.orders) || state.orders.length === 0) return []

      const dishGroups = groupOrdersByDish(state.orders, (order) => `${order.dish_name}_${order.station}`)

      return Object.values(dishGroups).map(dish => {
        const waitTimes = dish.orders.map(order =>
          TimeCalculator.calculateCookingDuration(order.order_time)
        )

        return {
          ...dish,
          maxWaitTime: Math.max(...waitTimes),
          avgWaitTime: waitTimes.reduce((sum, time) => sum + time, 0) / waitTimes.length,
          urgentCount: waitTimes.filter(time => time > TIME_THRESHOLDS.URGENT).length
        }
      })
    },

    // 排序后的订单
    sortedOrders: (state) => {
      const orders = [...state.filteredOrders]
      
      return orders.sort((a, b) => {
        let aValue, bValue

        switch (state.sortBy) {
          case 'order_time':
            aValue = new Date(a.order_time).getTime()
            bValue = new Date(b.order_time).getTime()
            break
          case 'table_number':
            aValue = a.table_number
            bValue = b.table_number
            break
          case 'dish_name':
            aValue = a.dish_name
            bValue = b.dish_name
            break
          case 'quantity':
            aValue = a.quantity
            bValue = b.quantity
            break
          case 'priority':
            const aPriority = OrderPrioritySelector.calculatePriority(a.order_time)
            const bPriority = OrderPrioritySelector.calculatePriority(b.order_time)
            aValue = OrderPrioritySelector.PRIORITY_WEIGHTS[aPriority]
            bValue = OrderPrioritySelector.PRIORITY_WEIGHTS[bPriority]
            break
          default:
            aValue = a[state.sortBy]
            bValue = b[state.sortBy]
        }

        if (state.sortOrder === 'asc') {
          return aValue > bValue ? 1 : -1
        } else {
          return aValue < bValue ? 1 : -1
        }
      })
    }
  },

  actions: {
    /**
     * 获取订单列表
     * @param {Object} params 查询参数
     */
    async fetchOrders(params = {}) {
      this.loading = true
      this.error = null

      try {
        const response = await ordersAPI.getOrders(params)
        
        if (response.success && response.data) {
          const isKitchenPoll = Boolean(params.station && params.station !== 'all')
          const newDataHash = this.calculateDataHash(response.data)
          const oldDataHash = this.calculateDataHash(this.orders)

          if (isKitchenPoll || newDataHash !== oldDataHash) {
            this.orders = response.data
            this.dataVersion++
            if (!isKitchenPoll) {
              console.log(`[订单数据更新] 检测到数据变化，版本: ${this.dataVersion}`)
            }
          }

          this.lastUpdated = new Date()
        } else {
          throw new Error(response.message || '获取订单数据失败')
        }

      } catch (error) {
        console.error('获取订单列表失败:', error)
        this.error = error.message || '获取数据失败'
        throw error
      } finally {
        this.loading = false
      }
    },

    /**
     * 获取当天订单数据
     * @param {Object} params 查询参数
     */
    async fetchTodayOrders(params = {}) {
      const today = new Date()
      // 设置当天的开始时间 (00:00:00)
      const startOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 0, 0, 0, 0)
      // 设置当天的结束时间 (23:59:59)  
      const endOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 23, 59, 59, 999)

      console.log(`[订单Store] 获取当天订单，时间范围: ${startOfDay.toISOString()} 到 ${endOfDay.toISOString()}`)

      return await this.fetchOrders({
        start_time: startOfDay.toISOString(),
        end_time: endOfDay.toISOString(),
        ...params
      })
    },

    /**
     * 厨房制作完成操作
     * @param {Object} completeData 制作完成数据
     */
    async completeCooking(completeData) {
      this.operationLoading = true
      let apiData = null

      try {
        // 自动选择最优订单
        const selectedOrders = OrderPrioritySelector.autoSelectOrders(
          completeData.orders || [],
          completeData.completeQuantity
        )
        
        // Fallback: 如果优先级选择器没有选择到订单，尝试手动选择第一个可用订单
        if (selectedOrders.length === 0 && completeData.orders.length > 0) {
          console.warn('[制作完成] 优先级选择器未选择到订单，尝试手动选择第一个')
          const firstOrder = completeData.orders[0]
          const fallbackOrder = {
            orderId: firstOrder._id || firstOrder.id,
            businessFlowId: firstOrder.business_flow_id || '',
            tableNumber: firstOrder.table_number,
            originalQuantity: firstOrder.quantity || 1,
            serveQuantity: Math.min(completeData.completeQuantity, firstOrder.quantity || 1),
            priority: 'normal',
            waitTime: 0
          }
          
          if (fallbackOrder.orderId && fallbackOrder.tableNumber) {
            selectedOrders.push(fallbackOrder)
          } else {
            console.error('[制作完成] Fallback订单数据也不完整:', fallbackOrder)
          }
        }

        apiData = {
          dish_name: completeData.dishName,
          station: completeData.station,
          complete_quantity: completeData.completeQuantity,
          orders: selectedOrders.map(order => ({
            order_id: order.orderId,
            business_flow_id: order.businessFlowId || undefined,
            table_number: order.tableNumber,
            complete_quantity: order.serveQuantity,
            original_quantity: order.originalQuantity
          })),
          operator_id: completeData.operatorId || 'system',
          ready_time: completeData.ready_time || new Date().toISOString() // 🆕 使用传入的ready_time
        }
        
        // 🔍 调试：打印最终API数据
        console.log('[制作完成] 发送到后端的数据:', JSON.stringify(apiData, null, 2))

        // ✅ 数据验证
        if (!apiData.dish_name || !apiData.station) {
          throw new Error('菜品名称和档口不能为空')
        }
        
        if (!apiData.orders || apiData.orders.length === 0) {
          throw new Error('没有可完成制作的订单')
        }
        
        // 验证订单数据完整性
        const invalidOrders = apiData.orders.filter(order => 
          !order.order_id || !order.table_number || !order.complete_quantity || !order.original_quantity
        )
        
        if (invalidOrders.length > 0) {
          console.error('[制作完成] 无效订单数据:', invalidOrders)
          throw new Error('订单数据不完整，请检查订单信息')
        }

        const response = await ordersAPI.completeCooking(apiData)

        if (response.success) {
          // 🆕 使用后端返回的准确时间更新本地订单状态
          const actualReadyTime = response.ready_time || apiData.ready_time
          console.log('[制作完成] 使用后端返回的制作完成时间:', actualReadyTime)
          
          this.updateLocalOrderStatus(selectedOrders, '已制作待上菜', {
            ready_time: actualReadyTime
          })

          return response
        } else {
          throw new Error(response.message || '制作完成操作失败')
        }

      } catch (error) {
        console.error('制作完成操作失败:', error)
        
        // 🔍 详细错误信息处理
        let errorMessage = '制作完成操作失败'
        
        if (error.response?.data) {
          // 后端返回的详细错误
          const backendData = error.response.data
          if (backendData.detail) {
            errorMessage = backendData.detail
          } else if (backendData.message) {
            errorMessage = backendData.message
          }
          console.error('[制作完成] 后端错误详情:', backendData)
        } else if (error.message) {
          errorMessage = error.message
        }
        
        // 创建新的错误对象，包含更有用的信息
        const enhancedError = new Error(errorMessage)
        enhancedError.originalError = error
        if (apiData) {
          enhancedError.apiData = apiData
        }
        
        throw enhancedError
      } finally {
        this.operationLoading = false
      }
    },

    /**
     * 更新本地订单状态
     * @param {Array} orders 要更新的订单列表
     * @param {String} newStatus 新状态
     * @param {Object} extraData 额外数据
     */
    updateLocalOrderStatus(orders, newStatus, extraData = {}) {
      orders.forEach(orderToUpdate => {
        const orderId = orderToUpdate.orderId
        const businessFlowId = orderToUpdate.businessFlowId
        const index = this.orders.findIndex(order =>
          order.id === orderId ||
          order._id === orderId ||
          order.business_flow_id === orderId ||
          (businessFlowId && order.business_flow_id === businessFlowId)
        )

        if (index === -1) return

        const current = this.orders[index]
        const serveQty = orderToUpdate.serveQuantity || 1
        const origQty = orderToUpdate.originalQuantity ?? current.quantity ?? 1

        if (serveQty < origQty) {
          this.orders[index] = {
            ...current,
            quantity: origQty - serveQty,
            dish_status: '待出餐',
            updated_at: new Date().toISOString()
          }
        } else {
          this.orders[index] = {
            ...current,
            dish_status: newStatus,
            updated_at: new Date().toISOString(),
            ...extraData
          }
        }
      })

      this.dataVersion++
    },

    /**
     * 更新订单筛选条件
     * @param {Object} filters 筛选条件
     */
    updateFilters(filters) {
      this.filters = { ...this.filters, ...filters }
    },

    /**
     * 设置排序方式
     * @param {String} sortBy 排序字段
     * @param {String} sortOrder 排序顺序
     */
    setSorting(sortBy, sortOrder = 'desc') {
      this.sortBy = sortBy
      this.sortOrder = sortOrder
    },

    /**
     * 选择订单
     * @param {Array} orders 要选择的订单
     */
    selectOrders(orders) {
      this.selectedOrders = [...orders]
    },

    /**
     * 清除选择
     */
    clearSelection() {
      this.selectedOrders = []
    },

    /**
     * 计算数据哈希值
     * @param {Array} orders 订单数据
     * @returns {String} 哈希值
     */
    calculateDataHash(orders) {
      const hashData = orders.map(order => ({
        id: order.id || order._id,
        dish_status: order.dish_status,
        quantity: order.quantity,
        updated_at: order.updated_at,
        placement: order.placement || null
      }))
      return JSON.stringify(hashData)
    },

    /**
     * 手动刷新数据
     */
    async refreshData() {
      await this.fetchTodayOrders()
    },

    /**
     * 重置状态
     */
    resetState() {
      this.orders = []
      this.selectedOrders = []
      this.filters = {
        station: 'all',
        status: 'all',
        tableNumber: '',
        date: ''
      }
      this.loading = false
      this.error = null
      this.lastUpdated = null
      this.dataVersion = 0
    },

    /**
     * 获取指定订单的详细信息
     * @param {String} orderId 订单ID
     * @returns {Object} 订单详情
     */
    getOrderDetail(orderId) {
      const order = this.orders.find(o => 
        o.id === orderId || o.business_flow_id === orderId
      )
      
      if (!order) return null

      const priorityInfo = OrderPrioritySelector.getOrderPriorityInfo(order)
      const cookingDuration = order.ready_time ? 
        TimeCalculator.calculateCookingDuration(order.order_time, order.ready_time) : 
        TimeCalculator.calculateCookingDuration(order.order_time)
      
      const servingDuration = order.served_time && order.ready_time ?
        TimeCalculator.calculateServingDuration(order.ready_time, order.served_time) :
        (order.ready_time ? TimeCalculator.calculateServingDuration(order.ready_time) : 0)

      return {
        ...order,
        priorityInfo,
        cookingDuration,
        servingDuration,
        totalDuration: order.served_time ? 
          TimeCalculator.calculateTotalDuration(order.order_time, order.served_time) :
          TimeCalculator.calculateTotalDuration(order.order_time),
        cookingDurationFormatted: TimeCalculator.formatDuration(cookingDuration),
        servingDurationFormatted: TimeCalculator.formatDuration(servingDuration)
      }
    }
  }
})

export default useOrdersStore 