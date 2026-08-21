<template>
  <view class="orders-container">
    <view class="page-header">
      <view class="header-bar">
        <text class="back-link" @click="goHome">← 返回</text>
        <view class="header-titles">
          <text class="page-title">订单查看</text>
          <text class="page-subtitle">按日期 / 状态 / 档口查询</text>
        </view>
        <view class="refresh-btn" @click="refreshData">
          <SvgIcon name="refresh-cw" :size="16" color="#1890ff" />
        </view>
      </view>
    </view>

    <view class="page-body">
    <!-- 筛选区域 -->
    <view class="filter-section">
      <view class="filter-grid">
        <view class="filter-item">
          <text class="filter-label">开始日期</text>
          <picker mode="date" :value="filters.startDate" @change="onStartDateChange">
            <view class="picker-field">
              <text class="picker-text">{{ filters.startDate || '选择开始日期' }}</text>
              <SvgIcon name="calendar" :size="14" color="#666" />
            </view>
          </picker>
        </view>

        <view class="filter-item">
          <text class="filter-label">结束日期</text>
          <picker mode="date" :value="filters.endDate" @change="onEndDateChange">
            <view class="picker-field">
              <text class="picker-text">{{ filters.endDate || '选择结束日期' }}</text>
              <SvgIcon name="calendar" :size="14" color="#666" />
            </view>
          </picker>
        </view>

        <view class="filter-item">
          <text class="filter-label">菜品状态</text>
          <picker :range="statusOptions" range-key="name" :value="statusIndex" @change="onStatusChange">
            <view class="picker-field">
              <text class="picker-text">{{ statusOptions[statusIndex].name }}</text>
              <SvgIcon name="chevron-down" :size="12" color="#666" />
            </view>
          </picker>
        </view>

        <view class="filter-item">
          <text class="filter-label">档口</text>
          <picker :range="stationOptions" range-key="name" :value="stationIndex" @change="onStationChange">
            <view class="picker-field">
              <text class="picker-text">{{ stationOptions[stationIndex].name }}</text>
              <SvgIcon name="chevron-down" :size="12" color="#666" />
            </view>
          </picker>
        </view>
      </view>

      <view class="filter-actions">
        <view class="action-btn search-btn" @click="searchOrders">
          <SvgIcon name="search" :size="14" color="#fff" /><text>查询</text>
        </view>
        <view class="action-btn reset-btn" @click="resetFilters">
          <SvgIcon name="refresh-cw" :size="14" color="#666" /><text>重置</text>
        </view>
        <view class="action-btn export-btn" @click="exportData">
          <SvgIcon name="upload" :size="14" color="#fff" /><text>导出</text>
        </view>
      </view>
    </view>

    <!-- 统计信息 -->
    <view class="stats-section" v-if="stats">
      <view class="stats-card">
        <view class="stats-title"><SvgIcon name="bar-chart" :size="14" color="#333" /><text>查询结果统计</text></view>
        <view class="stats-row">
          <view class="stat-item">
            <text class="stat-label">总订单</text>
            <text class="stat-value">{{ stats.total }}</text>
          </view>
          <view class="stat-item">
            <text class="stat-label">待出餐</text>
            <text class="stat-value pending">{{ stats.pending }}</text>
          </view>
          <view class="stat-item">
            <text class="stat-label">制作中</text>
            <text class="stat-value cooking">{{ stats.cooking }}</text>
          </view>
          <view class="stat-item">
            <text class="stat-label">已上菜</text>
            <text class="stat-value served">{{ stats.served }}</text>
          </view>
        </view>
      </view>
    </view>

    <!-- 订单列表 -->
    <view class="orders-list" v-if="orders.length > 0">
      <view class="list-header">
        <text class="result-count">共 {{ orders.length }} 条</text>
        <view class="sort-controls">
          <text class="sort-label">排序</text>
          <picker :range="sortOptions" range-key="name" :value="sortIndex" @change="onSortChange">
            <view class="sort-picker">
              <text>{{ sortOptions[sortIndex].name }}</text>
              <SvgIcon name="chevron-down" :size="12" color="#666" />
            </view>
          </picker>
        </view>
      </view>

      <scroll-view class="orders-scroll" scroll-y="true" :refresher-enabled="true"
                   @refresherrefresh="onRefresh" :refresher-triggered="refreshing">
        <view class="orders-grid">
        <view class="order-item" v-for="order in sortedOrders" :key="order._id || order.business_flow_id" @click="showOrderDetail(order)">
          <view class="order-header">
            <view class="order-id">
              <text class="id-label">订单号</text>
              <text class="id-value">{{ order.business_flow_id }}</text>
            </view>
            <view class="order-status" :class="getStatusClass(order.dish_status)">
              <text>{{ order.dish_status }}</text>
            </view>
          </view>

          <view class="order-content">
            <view class="dish-info">
              <view class="dish-heading">
                <view class="dish-name"><SvgIcon name="utensils" :size="14" color="#333" /><text>{{ order.dish_name }}</text></view>
                <view v-if="canonicalOrderNotes(order.notes)" class="dish-notes">{{ canonicalOrderNotes(order.notes) }}</view>
              </view>
              <view class="dish-details">
                <view class="dish-detail"><SvgIcon name="store" :size="12" color="#666" /><text>{{ getStationName(order.station) }}</text></view>
                <view class="dish-detail"><SvgIcon name="armchair" :size="12" color="#666" /><text>{{ order.table_number }}桌</text></view>
                <view class="dish-detail"><SvgIcon name="package" :size="12" color="#666" /><text>{{ order.quantity }}份</text></view>
                <view class="dish-detail"><SvgIcon name="banknote" :size="12" color="#666" /><text>¥{{ order.price || 0 }}</text></view>
              </view>
            </view>

            <view class="time-info">
              <text class="time-label">下单</text>
              <text class="time-value">{{ formatTime(order.order_time) }}</text>
              <view class="time-details" v-if="order.ready_time || order.served_time">
                <view class="time-detail" v-if="order.ready_time">
                  <SvgIcon name="cooking-pot" :size="12" color="#666" /><text>完成 {{ formatTime(order.ready_time) }}</text>
                </view>
                <view class="time-detail" v-if="order.served_time">
                  <SvgIcon name="truck" :size="12" color="#666" /><text>上菜 {{ formatTime(order.served_time) }}</text>
                </view>
              </view>
            </view>
          </view>

          <view class="order-footer">
            <text class="category-tag">{{ order.category }}</text>
            <view class="priority-tag" :class="getPriorityClass(order.priority)">
              <text>{{ getPriorityText(order.priority) }}</text>
            </view>
          </view>
        </view>
        </view>
      </scroll-view>
    </view>

    <!-- 空状态 -->
    <view class="empty-state" v-else-if="!loading">
      <SvgIcon class="empty-icon" name="inbox" :size="40" color="#999" />
      <text class="empty-text">暂无订单数据</text>
      <text class="empty-hint">请调整筛选条件后重新查询</text>
    </view>

    <!-- 加载状态 -->
    <view class="loading-state" v-if="loading">
      <SvgIcon class="loading-icon" name="refresh-cw" :size="24" color="#1890ff" />
      <text class="loading-text">正在加载订单数据...</text>
    </view>
    </view>

    <!-- 订单详情弹窗 -->
    <view class="modal-overlay" v-if="showDetailModal" @click="hideOrderDetail">
      <view class="order-detail-modal" @click.stop>
        <view class="modal-header">
          <view class="modal-title"><SvgIcon name="clipboard" :size="16" color="#333" /><text>订单详情</text></view>
          <view class="modal-close" @click="hideOrderDetail">
            <SvgIcon name="x" :size="16" color="#666" />
          </view>
        </view>

        <scroll-view class="modal-content" scroll-y="true" v-if="selectedOrder">
          <view class="detail-section">
            <text class="section-title">基本信息</text>
            <view class="detail-row">
              <text class="detail-label">订单ID:</text>
              <text class="detail-value">{{ selectedOrder.business_flow_id }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">桌号:</text>
              <text class="detail-value">{{ selectedOrder.table_number }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">菜品名称:</text>
              <text class="detail-value">{{ selectedOrder.dish_name }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">档口:</text>
              <text class="detail-value">{{ getStationName(selectedOrder.station) }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">状态:</text>
              <text class="detail-value" :class="getStatusClass(selectedOrder.dish_status)">{{ selectedOrder.dish_status }}</text>
            </view>
          </view>

          <view class="detail-section">
            <text class="section-title">数量信息</text>
            <view class="detail-row">
              <text class="detail-label">总数量:</text>
              <text class="detail-value">{{ selectedOrder.quantity }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">已出数量:</text>
              <text class="detail-value">{{ selectedOrder.served_quantity || 0 }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">待出数量:</text>
              <text class="detail-value">{{ selectedOrder.quantity - (selectedOrder.served_quantity || 0) }}</text>
            </view>
          </view>

          <view class="detail-section">
            <text class="section-title">价格信息</text>
            <view class="detail-row">
              <text class="detail-label">单价:</text>
              <text class="detail-value">¥{{ selectedOrder.price || 0 }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">总金额:</text>
              <text class="detail-value">¥{{ selectedOrder.total_amount || 0 }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">分类:</text>
              <text class="detail-value">{{ selectedOrder.category }}</text>
            </view>
          </view>

          <view class="detail-section">
            <text class="section-title">时间信息</text>
            <view class="detail-row">
              <text class="detail-label">下单时间:</text>
              <text class="detail-value">{{ formatTime(selectedOrder.order_time) }}</text>
            </view>
            <view class="detail-row" v-if="selectedOrder.ready_time">
              <text class="detail-label">制作完成:</text>
              <text class="detail-value">{{ formatTime(selectedOrder.ready_time) }}</text>
            </view>
            <view class="detail-row" v-if="selectedOrder.served_time">
              <text class="detail-label">上菜时间:</text>
              <text class="detail-value">{{ formatTime(selectedOrder.served_time) }}</text>
            </view>
            <view class="detail-row" v-if="selectedOrder.refund_time">
              <text class="detail-label">退菜时间:</text>
              <text class="detail-value">{{ formatTime(selectedOrder.refund_time) }}</text>
            </view>
          </view>

          <view class="detail-section">
            <text class="section-title">其他信息</text>
            <view class="detail-row">
              <text class="detail-label">创建时间:</text>
              <text class="detail-value">{{ formatTime(selectedOrder.created_at) }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">更新时间:</text>
              <text class="detail-value">{{ formatTime(selectedOrder.updated_at) }}</text>
            </view>
            <view class="detail-row">
              <text class="detail-label">优先级:</text>
              <text class="detail-value" :class="getPriorityClass(selectedOrder.priority)">{{ getPriorityText(selectedOrder.priority) }}</text>
            </view>
          </view>
        </scroll-view>
      </view>
    </view>
  </view>
</template>

<script>
import { ref, computed, onMounted, reactive } from 'vue'
import { request } from '../../utils/request.js'
import { useStationsStore } from '../../stores/stations.js'
import SvgIcon from '../../components/SvgIcon/SvgIcon.vue'
import { canonicalOrderNotes } from '../../utils/orderNotes.js'

export default {
  name: 'OrdersPage',
  components: { SvgIcon },
  setup() {
    const stationsStore = useStationsStore()

    // 响应式数据
    const loading = ref(false)
    const refreshing = ref(false)
    const orders = ref([])
    const stats = ref(null)
    const showDetailModal = ref(false)
    const selectedOrder = ref(null)

    // 筛选条件
    const filters = reactive({
      startDate: '',
      endDate: '',
      status: '',
      station: ''
    })

    // 筛选选项
    const statusOptions = ref([
      { key: '', name: '全部状态' },
      { key: '待出餐', name: '待出餐' },
      { key: '已制作待上菜', name: '已制作待上菜' },
      { key: '已上菜', name: '已上菜' },
      { key: '退菜', name: '退菜' }
    ])

    const stationOptions = computed(() => [
      { key: '', name: '全部档口' },
      ...stationsStore.stationList.map(({ id, name }) => ({ key: id, name }))
    ])

    const sortOptions = ref([
      { key: 'order_time_desc', name: '下单时间(最新)' },
      { key: 'order_time_asc', name: '下单时间(最早)' },
      { key: 'price_desc', name: '价格(高到低)' },
      { key: 'price_asc', name: '价格(低到高)' },
      { key: 'table_number_asc', name: '桌号(升序)' }
    ])

    // 当前选择的索引
    const statusIndex = ref(0)
    const stationIndex = ref(0)
    const sortIndex = ref(0)

    // 计算属性 - 排序后的订单
    const sortedOrders = computed(() => {
      const sortOption = sortOptions.value[sortIndex.value]
      let sorted = [...orders.value]

      switch (sortOption.key) {
        case 'order_time_desc':
          sorted.sort((a, b) => new Date(b.order_time) - new Date(a.order_time))
          break
        case 'order_time_asc':
          sorted.sort((a, b) => new Date(a.order_time) - new Date(b.order_time))
          break
        case 'price_desc':
          sorted.sort((a, b) => b.price - a.price)
          break
        case 'price_asc':
          sorted.sort((a, b) => a.price - b.price)
          break
        case 'table_number_asc':
          sorted.sort((a, b) => parseInt(a.table_number) - parseInt(b.table_number))
          break
        default:
          break
      }

      return sorted
    })

    // 方法
    const formatTime = (timeStr) => {
      if (!timeStr) return '无'
      try {
        const date = new Date(timeStr)
        return date.toLocaleString('zh-CN', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit'
        })
      } catch (error) {
        return timeStr
      }
    }

    const getStationName = (stationKey) => {
      if (!stationKey) return '未分配档口'
      return stationsStore.getStationById(stationKey)?.name || stationKey
    }

    const getStatusClass = (status) => {
      switch (status) {
        case '待出餐': return 'status-pending'
        case '已制作待上菜': return 'status-ready'
        case '已上菜': return 'status-served'
        case '退菜': return 'status-refunded'
        default: return 'status-unknown'
      }
    }

    const getPriorityClass = (priority) => {
      switch (priority) {
        case 'urgent': return 'priority-urgent'
        case 'high': return 'priority-high'
        case 'normal': return 'priority-normal'
        default: return 'priority-normal'
      }
    }

    const getPriorityText = (priority) => {
      switch (priority) {
        case 'urgent': return '紧急'
        case 'high': return '高优先级'
        case 'normal': return '正常'
        default: return '正常'
      }
    }

    // 事件处理
    const onStartDateChange = (e) => {
      filters.startDate = e.detail.value
    }

    const onEndDateChange = (e) => {
      filters.endDate = e.detail.value
    }

    const onStatusChange = (e) => {
      statusIndex.value = e.detail.value
      filters.status = statusOptions.value[e.detail.value].key
    }

    const onStationChange = (e) => {
      stationIndex.value = e.detail.value
      filters.station = stationOptions.value[e.detail.value].key
    }

    const onSortChange = (e) => {
      sortIndex.value = e.detail.value
    }

    const resetFilters = () => {
      filters.startDate = ''
      filters.endDate = ''
      filters.status = ''
      filters.station = ''
      statusIndex.value = 0
      stationIndex.value = 0
      orders.value = []
      stats.value = null
      
      uni.showToast({
        title: '筛选条件已重置',
        icon: 'success'
      })
    }

    const searchOrders = async () => {
      if (!filters.startDate) {
        uni.showToast({
          title: '请选择开始日期',
          icon: 'none'
        })
        return
      }

      loading.value = true
      
      try {
        const queryData = {
          start_date: filters.startDate,
          end_date: filters.endDate || filters.startDate,
          limit: 1000
        }
        if (filters.status) queryData.status = filters.status
        if (filters.station) queryData.station = filters.station

        const response = await request({
          url: '/api/orders/search',
          method: 'GET',
          data: queryData
        })

        if (response && response.success) {
          orders.value = (response.orders || []).map(normalizeOrder)
          
          if (response.stats) {
            stats.value = response.stats
          } else {
            calculateStats()
          }
          
          uni.showToast({
            title: `找到 ${orders.value.length} 个订单`,
            icon: 'success'
          })
        } else {
          orders.value = []
          stats.value = null
          uni.showToast({
            title: '未找到订单数据',
            icon: 'none'
          })
        }
      } catch (error) {
        console.error('搜索订单失败:', error)
        uni.showToast({
          title: '查询失败: ' + (error.message || '请检查网络连接'),
          icon: 'none'
        })
        orders.value = []
        stats.value = null
      } finally {
        loading.value = false
      }
    }

    const normalizeOrder = (order) => {
      return {
        ...order,
        price: order.price || 0,
        total_amount: order.total_amount || (order.price || 0) * (order.quantity || 1),
        served_quantity: order.served_quantity || 0,
        category: order.category || '未分类',
        priority: order.priority || 'normal'
      }
    }

    const calculateStats = () => {
      if (!orders.value.length) {
        stats.value = null
        return
      }

      const statusCount = {
        total: orders.value.length,
        pending: 0,
        cooking: 0,
        served: 0,
        refunded: 0
      }

      orders.value.forEach(order => {
        switch (order.dish_status) {
          case '待出餐':
            statusCount.pending++
            break
          case '已制作待上菜':
            statusCount.cooking++
            break
          case '已上菜':
            statusCount.served++
            break
          case '退菜':
            statusCount.refunded++
            break
        }
      })

      stats.value = statusCount
    }

    const showOrderDetail = (order) => {
      selectedOrder.value = order
      showDetailModal.value = true
    }

    const hideOrderDetail = () => {
      showDetailModal.value = false
      selectedOrder.value = null
    }

    const refreshData = async () => {
      if (filters.startDate) {
        await searchOrders()
      } else {
        uni.showToast({
          title: '请先设置筛选条件',
          icon: 'none'
        })
      }
    }

    const onRefresh = async () => {
      refreshing.value = true
      await refreshData()
      refreshing.value = false
    }

    const exportData = () => {
      if (!orders.value.length) {
        uni.showToast({
          title: '暂无数据可导出',
          icon: 'none'
        })
        return
      }

      // 生成CSV格式数据
      const csvHeader = '订单号,桌号,菜品名称,档口,状态,数量,已出数量,价格,分类,下单时间\n'
      const csvData = orders.value.map(order => {
        return [
          order.business_flow_id,
          order.table_number,
          order.dish_name,
          getStationName(order.station),
          order.dish_status,
          order.quantity,
          order.served_quantity,
          order.price,
          order.category,
          formatTime(order.order_time)
        ].join(',')
      }).join('\n')

      const csvContent = csvHeader + csvData

      // 尝试保存文件或复制到剪贴板
      uni.setClipboardData({
        data: csvContent,
        success: () => {
          uni.showToast({
            title: '数据已复制到剪贴板',
            icon: 'success'
          })
        },
        fail: () => {
          uni.showToast({
            title: '导出失败',
            icon: 'none'
          })
        }
      })
    }

    onMounted(async () => {
      const today = new Date()
      const todayStr = today.toISOString().split('T')[0]
      filters.startDate = todayStr
      filters.endDate = todayStr
      await searchOrders()
    })

    const goHome = () => {
      uni.reLaunch({ url: '/pages/index/index' })
    }

    return {
      // 数据
      loading,
      refreshing,
      orders,
      stats,
      filters,
      showDetailModal,
      selectedOrder,
      // 选项
      statusOptions,
      stationOptions,
      sortOptions,
      // 索引
      statusIndex,
      stationIndex,
      sortIndex,
      // 计算属性
      sortedOrders,
      // 方法
      goHome,
      formatTime,
      getStationName,
      getStatusClass,
      getPriorityClass,
      getPriorityText,
      onStartDateChange,
      onEndDateChange,
      onStatusChange,
      onStationChange,
      onSortChange,
      resetFilters,
      searchOrders,
      showOrderDetail,
      hideOrderDetail,
      refreshData,
      onRefresh,
      exportData,
      canonicalOrderNotes
    }
  }
}
</script>

<style scoped>
.orders-container {
  width: 100%;
  min-height: 100vh;
  background: #f5f5f5;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

.page-header {
  padding: 24upx 24upx 12upx;
  padding-top: calc(24upx + env(safe-area-inset-top));
}

.header-bar {
  display: flex;
  align-items: flex-start;
  gap: 16upx;
}

.back-link {
  flex-shrink: 0;
  font-size: 26upx;
  color: #1890ff;
  font-weight: 500;
  padding: 8upx 0;
}

.header-titles {
  flex: 1;
  min-width: 0;
}

.page-title {
  display: block;
  font-size: 40upx;
  font-weight: 700;
  color: #1a1a1a;
  margin-bottom: 4upx;
}

.page-subtitle {
  display: block;
  font-size: 22upx;
  color: #666666;
}

.refresh-btn {
  flex-shrink: 0;
  width: 64upx;
  height: 64upx;
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 2upx 8upx rgba(0, 0, 0, 0.06);
  display: flex;
  align-items: center;
  justify-content: center;
}

.page-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0 24upx 32upx;
  box-sizing: border-box;
}

.filter-section,
.stats-card,
.orders-list {
  background: #ffffff;
  border-radius: 16upx;
  box-shadow: 0 2upx 8upx rgba(0, 0, 0, 0.06);
}

.filter-section {
  padding: 24upx;
  margin-bottom: 16upx;
}

.filter-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16upx;
}

.filter-item {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.filter-label {
  font-size: 22upx;
  color: #666666;
  margin-bottom: 8upx;
}

.picker-field {
  background: #f7f8fa;
  padding: 18upx 20upx;
  border-radius: 10upx;
  border: 2upx solid #e9ecef;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12upx;
  min-height: 72upx;
  box-sizing: border-box;
}

.picker-text {
  font-size: 26upx;
  color: #1a1a1a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.filter-actions {
  display: flex;
  gap: 12upx;
  margin-top: 20upx;
  flex-wrap: wrap;
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8upx;
  flex: 1 1 140upx;
  min-width: 140upx;
  padding: 20upx 16upx;
  border-radius: 10upx;
  font-size: 26upx;
  font-weight: 600;
}

.search-btn {
  background: #1890ff;
  color: #ffffff;
}

.reset-btn {
  background: #f0f0f0;
  color: #666666;
}

.export-btn {
  background: #52c41a;
  color: #ffffff;
}

.stats-section {
  margin-bottom: 16upx;
}

.stats-card {
  padding: 24upx;
}

.stats-title {
  display: flex;
  align-items: center;
  gap: 8upx;
  font-size: 26upx;
  font-weight: 600;
  color: #1a1a1a;
  margin-bottom: 16upx;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12upx;
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12upx 8upx;
  background: #f7f8fa;
  border-radius: 10upx;
}

.stat-label {
  font-size: 20upx;
  color: #666666;
  margin-bottom: 6upx;
}

.stat-value {
  font-size: 30upx;
  font-weight: 700;
  color: #1a1a1a;
}

.stat-value.pending { color: #fa8c16; }
.stat-value.cooking { color: #1890ff; }
.stat-value.served { color: #52c41a; }

.orders-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 360upx;
  overflow: hidden;
}

.list-header {
  padding: 18upx 24upx;
  background: #f7f8fa;
  border-bottom: 1upx solid #f0f0f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16upx;
  flex-wrap: wrap;
}

.result-count {
  font-size: 24upx;
  color: #1a1a1a;
  font-weight: 600;
}

.sort-controls {
  display: flex;
  align-items: center;
  gap: 10upx;
}

.sort-label {
  font-size: 22upx;
  color: #666666;
}

.sort-picker {
  background: #ffffff;
  padding: 10upx 16upx;
  border-radius: 8upx;
  border: 2upx solid #e9ecef;
  display: flex;
  align-items: center;
  gap: 8upx;
  font-size: 22upx;
  color: #1a1a1a;
}

.orders-scroll {
  flex: 1;
  height: 0;
  min-height: 280upx;
}

.orders-grid {
  display: flex;
  flex-direction: column;
}

.order-item {
  padding: 24upx;
  border-bottom: 1upx solid #f0f0f0;
}

.order-item:active {
  background: #f7faff;
}

.order-item:last-child {
  border-bottom: none;
}

.order-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12upx;
  margin-bottom: 16upx;
}

.order-id {
  display: flex;
  flex-direction: column;
  gap: 4upx;
  min-width: 0;
  flex: 1;
}

.id-label {
  font-size: 20upx;
  color: #999999;
}

.id-value {
  font-size: 24upx;
  color: #1a1a1a;
  font-weight: 600;
  word-break: break-all;
}

.order-status {
  padding: 6upx 14upx;
  border-radius: 999upx;
  font-size: 20upx;
  font-weight: 600;
  flex-shrink: 0;
}

.status-pending {
  background: #fff7e6;
  color: #d46b08;
}

.status-ready {
  background: #e6f7ff;
  color: #096dd9;
}

.status-served {
  background: #f6ffed;
  color: #389e0d;
}

.status-refunded {
  background: #fff1f0;
  color: #cf1322;
}

.order-content {
  margin-bottom: 16upx;
}

.dish-heading {
  margin-bottom: 12upx;
}

.dish-name {
  display: flex;
  align-items: center;
  gap: 8upx;
  font-size: 30upx;
  font-weight: 600;
  color: #1a1a1a;
}

.dish-notes {
  font-size: 24upx;
  font-weight: 600;
  color: #64748b;
  margin-top: 4upx;
}

.dish-details {
  display: flex;
  flex-wrap: wrap;
  gap: 12upx 20upx;
  margin-bottom: 12upx;
}

.dish-detail {
  display: flex;
  align-items: center;
  gap: 6upx;
  font-size: 22upx;
  color: #666666;
}

.time-info {
  background: #f7f8fa;
  padding: 16upx;
  border-radius: 10upx;
}

.time-label {
  font-size: 22upx;
  color: #666666;
  margin-right: 8upx;
}

.time-value {
  font-size: 22upx;
  color: #1a1a1a;
  font-weight: 600;
}

.time-details {
  margin-top: 8upx;
  display: flex;
  flex-direction: column;
  gap: 6upx;
}

.time-detail {
  display: flex;
  align-items: center;
  gap: 6upx;
  font-size: 20upx;
  color: #666666;
}

.order-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12upx;
}

.category-tag,
.priority-tag {
  padding: 6upx 12upx;
  border-radius: 8upx;
  font-size: 20upx;
}

.category-tag {
  background: #f0f0f0;
  color: #595959;
}

.priority-urgent {
  background: #fff1f0;
  color: #cf1322;
  font-weight: 600;
}

.priority-high {
  background: #fff7e6;
  color: #d46b08;
  font-weight: 600;
}

.priority-normal {
  background: #f6ffed;
  color: #389e0d;
}

.empty-state,
.loading-state {
  text-align: center;
  padding: 80upx 40upx;
}

.empty-icon,
.loading-icon {
  margin-bottom: 16upx;
}

.empty-text {
  display: block;
  font-size: 30upx;
  color: #1a1a1a;
  margin-bottom: 8upx;
}

.empty-hint,
.loading-text {
  display: block;
  font-size: 24upx;
  color: #666666;
}

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
  padding: 32upx;
  box-sizing: border-box;
}

.order-detail-modal {
  background: #ffffff;
  border-radius: 16upx;
  width: min(920upx, 100%);
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.modal-header {
  padding: 24upx;
  background: #f7f8fa;
  border-bottom: 1upx solid #f0f0f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-title {
  display: flex;
  align-items: center;
  gap: 8upx;
  font-size: 30upx;
  font-weight: 600;
  color: #1a1a1a;
}

.modal-close {
  width: 56upx;
  height: 56upx;
  border-radius: 50%;
  background: #e9ecef;
  display: flex;
  justify-content: center;
  align-items: center;
}

.modal-content {
  flex: 1;
  padding: 24upx;
  max-height: 70vh;
}

.detail-section {
  margin-bottom: 28upx;
}

.section-title {
  font-size: 26upx;
  font-weight: 600;
  color: #1a1a1a;
  margin-bottom: 12upx;
  padding-bottom: 8upx;
  border-bottom: 1upx solid #f0f0f0;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20upx;
  padding: 12upx 0;
  border-bottom: 1upx solid #f7f7f7;
}

.detail-row:last-child {
  border-bottom: none;
}

.detail-label {
  font-size: 24upx;
  color: #666666;
  flex-shrink: 0;
}

.detail-value {
  font-size: 24upx;
  color: #1a1a1a;
  font-weight: 600;
  text-align: right;
  word-break: break-all;
}

/* 窄屏：筛选单列、按钮纵向 */
@media screen and (max-width: 599upx) {
  .filter-grid {
    grid-template-columns: 1fr;
  }

  .filter-actions {
    flex-direction: column;
  }

  .action-btn {
    flex: none;
    width: 100%;
    min-width: 0;
  }

  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }
}

/* 中等宽度：筛选 2x2，列表仍单列 */
@media screen and (min-width: 750upx) {
  .page-header,
  .page-body {
    max-width: 1400upx;
    width: 100%;
    margin-left: auto;
    margin-right: auto;
  }

  .page-header {
    padding-left: 40upx;
    padding-right: 40upx;
  }

  .page-body {
    padding-left: 40upx;
    padding-right: 40upx;
  }

  .filter-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}

/* 宽屏：订单双列卡片 */
@media screen and (min-width: 1024upx) {
  .page-title {
    font-size: 44upx;
  }

  .orders-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
  }

  .order-item {
    border-bottom: 1upx solid #f0f0f0;
    border-right: 1upx solid #f0f0f0;
  }

  .order-item:nth-child(2n) {
    border-right: none;
  }

  .order-detail-modal {
    width: min(1000upx, 92vw);
  }

  .modal-content {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0 32upx;
  }
}

/* 手机横屏：压低顶栏与筛选 */
@media screen and (max-height: 500upx) and (orientation: landscape) {
  .page-header {
    padding: 12upx 20upx 8upx;
  }

  .page-title {
    font-size: 30upx;
  }

  .page-subtitle {
    display: none;
  }

  .page-body {
    padding: 0 20upx 20upx;
  }

  .filter-section {
    padding: 16upx;
  }

  .filter-grid {
    grid-template-columns: repeat(4, 1fr);
    gap: 10upx;
  }

  .picker-field {
    min-height: 56upx;
    padding: 10upx 12upx;
  }

  .stats-card {
    padding: 16upx;
  }

  .orders-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
}
</style>