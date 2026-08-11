<template>
  <view class="detail-modal" v-if="visible" @click="handleMaskClick">
    <view class="modal-content" @click.stop>
      <!-- 模态框头部 -->
      <view class="modal-header">
        <text class="modal-title">订单详情</text>
        <view class="close-btn" @click="handleClose">
          <text class="close-icon">×</text>
        </view>
      </view>

      <!-- 菜品基本信息 -->
      <view class="dish-summary" v-if="dishData">
        <view class="dish-title">
          <text class="dish-name">{{ dishData.name }}</text>
          <view class="dish-tags">
            <text class="station-tag">{{ stationName }}</text>
            <text class="priority-tag" :class="priorityClass">{{ priorityText }}</text>
          </view>
        </view>
        
        <view class="dish-stats">
          <view class="stat-item">
            <text class="stat-label">总数量</text>
            <text class="stat-value">{{ dishData.total_quantity }}份</text>
          </view>
          <view class="stat-item" v-if="mode === 'kitchen'">
            <text class="stat-label">待制作</text>
            <text class="stat-value pending">{{ dishData.pending_quantity }}份</text>
          </view>
          <view class="stat-item" v-if="mode === 'delivery'">
            <text class="stat-label">待上菜</text>
            <text class="stat-value ready">{{ dishData.ready_quantity }}份</text>
          </view>
          <view class="stat-item">
            <text class="stat-label">{{ timeLabel }}</text>
            <text class="stat-value" :class="timeClass">{{ timeValue }}</text>
          </view>
        </view>
      </view>

      <!-- 订单列表 -->
      <view class="orders-section">
        <view class="section-header">
          <text class="section-title">相关订单 ({{ orderCount }}单)</text>
          <view class="filter-buttons">
            <text 
              class="filter-btn" 
              :class="{ active: orderFilter === 'all' }" 
              @click="setOrderFilter('all')"
            >
              全部
            </text>
            <text 
              class="filter-btn" 
              :class="{ active: orderFilter === 'pending' }" 
              @click="setOrderFilter('pending')"
            >
              {{ mode === 'kitchen' ? '待制作' : '待上菜' }}
            </text>
          </view>
        </view>
        
        <scroll-view class="orders-list" scroll-y>
          <view 
            v-for="order in filteredOrders" 
            :key="order.id"
            class="order-item"
            :class="orderItemClass(order)"
          >
            <view class="order-header">
              <view class="order-info">
                <text class="table-number">{{ order.table_number }}</text>
                <text class="order-time">{{ formatOrderTime(order.order_time) }}</text>
              </view>
              <view class="order-status">
                <text class="quantity">{{ order.quantity }}份</text>
                <text class="status-text" :class="getStatusClass(order)">
                  {{ getStatusText(order) }}
                </text>
              </view>
            </view>
            
            <view class="order-details">
              <view class="time-info">
                <text class="time-label">下单时间:</text>
                <text class="time-value">{{ formatDetailTime(order.order_time) }}</text>
              </view>
              <view class="time-info" v-if="order.cooking_completed_time && mode === 'delivery'">
                <text class="time-label">制作完成:</text>
                <text class="time-value">{{ formatDetailTime(order.cooking_completed_time) }}</text>
              </view>
              <view class="time-info">
                <text class="time-label">{{ mode === 'kitchen' ? '制作时长:' : '待上菜时长:' }}</text>
                <text class="duration-value" :class="getDurationClass(order)">
                  {{ formatOrderDuration(order) }}
                </text>
              </view>
              <view class="time-info" v-if="order.notes">
                <text class="time-label">备注:</text>
                <text class="notes-value">{{ order.notes }}</text>
              </view>
            </view>

            <!-- 单个订单操作按钮 -->
            <view class="order-actions" v-if="showOrderActions">
              <button 
                class="order-action-btn"
                :class="{ 'btn-disabled': order.processing }"
                :disabled="order.processing"
                @click="handleOrderAction(order)"
              >
                <text class="btn-text">
                  {{ order.processing ? '处理中...' : (mode === 'kitchen' ? '完成制作' : '完成上菜') }}
                </text>
              </button>
            </view>
          </view>
          
          <view v-if="filteredOrders.length === 0" class="empty-orders">
            <text class="empty-text">暂无{{ orderFilter === 'pending' ? (mode === 'kitchen' ? '待制作' : '待上菜') : '' }}订单</text>
          </view>
        </scroll-view>
      </view>

      <!-- 底部操作按钮 -->
      <view class="modal-footer" v-if="showBatchActions">
        <button 
          class="footer-btn secondary-btn" 
          @click="handleClose"
        >
          <text class="btn-text">关闭</text>
        </button>
        <button 
          class="footer-btn primary-btn"
          :class="{ 'btn-disabled': batchProcessing || !hasPendingOrders }"
          :disabled="batchProcessing || !hasPendingOrders"
          @click="handleBatchAction"
        >
          <text class="btn-text">
            {{ batchProcessing ? '处理中...' : getBatchActionText() }}
          </text>
        </button>
      </view>
    </view>
  </view>
</template>

<script>
import { ref, computed, watch } from 'vue'
import { useOrdersStore } from '../../stores/orders.js'
import { useDeliveryStore } from '../../stores/delivery.js'
import { useStationsStore } from '../../stores/stations.js'
import { TimeCalculator } from '../../utils/timeCalculator.js'
import { PRIORITY_LEVELS } from '../../utils/constants.js'

export default {
  name: 'DetailModal',
  
  props: {
    visible: {
      type: Boolean,
      default: false
    },
    dishData: {
      type: Object,
      default: () => null
    },
    mode: {
      type: String,
      default: 'kitchen', // 'kitchen' 或 'delivery'
      validator: (value) => ['kitchen', 'delivery'].includes(value)
    },
    showOrderActions: {
      type: Boolean,
      default: true
    },
    showBatchActions: {
      type: Boolean,
      default: true
    }
  },
  
  emits: ['close', 'order-action', 'batch-action'],
  
  setup(props, { emit }) {
    const ordersStore = useOrdersStore()
    const deliveryStore = useDeliveryStore()
    const stationsStore = useStationsStore()
    
    // 响应式数据
    const orderFilter = ref('all') // 'all', 'pending'
    const batchProcessing = ref(false)
    
    // 计算属性
    const stationName = computed(() => {
      if (!props.dishData?.station) return ''
      const station = stationsStore.getStationById(props.dishData.station)
      return station ? station.name : props.dishData.station
    })
    
    const priorityClass = computed(() => {
      return `priority-${props.dishData?.priority || 'normal'}`
    })
    
    const priorityText = computed(() => {
      const priority = PRIORITY_LEVELS[props.dishData?.priority || 'normal']
      return priority ? priority.text : '普通'
    })
    
    const timeLabel = computed(() => {
      return props.mode === 'kitchen' ? '制作时长' : '待上菜时长'
    })
    
    const timeValue = computed(() => {
      if (!props.dishData) return ''
      const duration = props.mode === 'kitchen' 
        ? props.dishData.cooking_duration 
        : props.dishData.serving_duration
      return TimeCalculator.formatDuration(duration)
    })
    
    const timeClass = computed(() => {
      if (!props.dishData) return ''
      const duration = props.mode === 'kitchen' 
        ? props.dishData.cooking_duration 
        : props.dishData.serving_duration
      const level = TimeCalculator.getOvertimeLevel(duration, props.mode)
      return `time-${level}`
    })
    
    const orderCount = computed(() => {
      return props.dishData?.orders?.length || 0
    })
    
    const filteredOrders = computed(() => {
      if (!props.dishData?.orders) return []
      
      const orders = props.dishData.orders
      if (orderFilter.value === 'all') {
        return orders
      } else if (orderFilter.value === 'pending') {
        return orders.filter(order => {
          if (props.mode === 'kitchen') {
            return order.dish_status === '待制作' || order.dish_status === '制作中'
          } else {
            return order.dish_status === '已制作待上菜'
          }
        })
      }
      return orders
    })
    
    const hasPendingOrders = computed(() => {
      if (!props.dishData?.orders) return false
      return filteredOrders.value.some(order => {
        if (props.mode === 'kitchen') {
          return order.dish_status === '待制作' || order.dish_status === '制作中'
        } else {
          return order.dish_status === '已制作待上菜'
        }
      })
    })
    
    // 方法
    const handleMaskClick = () => {
      handleClose()
    }
    
    const handleClose = () => {
      emit('close')
    }
    
    const setOrderFilter = (filter) => {
      orderFilter.value = filter
    }
    
    const orderItemClass = (order) => {
      const classes = []
      
      // 根据状态添加类
      if (order.dish_status) {
        classes.push(`status-${order.dish_status.replace(/\s+/g, '-')}`)
      }
      
      // 根据时长添加紧急程度类
      const duration = props.mode === 'kitchen' 
        ? order.cooking_duration 
        : order.serving_duration
      const level = TimeCalculator.getOvertimeLevel(duration, props.mode)
      classes.push(`time-${level}`)
      
      // 处理中状态
      if (order.processing) {
        classes.push('processing')
      }
      
      return classes
    }
    
    const getStatusClass = (order) => {
      if (props.mode === 'kitchen') {
        if (order.dish_status === '待制作') return 'status-pending'
        if (order.dish_status === '制作中') return 'status-cooking'
        if (order.dish_status === '已制作待上菜') return 'status-ready'
      } else {
        if (order.dish_status === '已制作待上菜') return 'status-ready'
        if (order.dish_status === '已上菜') return 'status-served'
      }
      return 'status-normal'
    }
    
    const getStatusText = (order) => {
      return order.dish_status || '未知状态'
    }
    
    const getDurationClass = (order) => {
      const duration = props.mode === 'kitchen' 
        ? order.cooking_duration 
        : order.serving_duration
      const level = TimeCalculator.getOvertimeLevel(duration, props.mode)
      return `duration-${level}`
    }
    
    const formatOrderTime = (timeStr) => {
      if (!timeStr) return ''
      return TimeCalculator.formatTime(new Date(timeStr), 'HH:mm')
    }
    
    const formatDetailTime = (timeStr) => {
      if (!timeStr) return ''
      return TimeCalculator.formatTime(new Date(timeStr), 'MM-dd HH:mm:ss')
    }
    
    const formatOrderDuration = (order) => {
      const duration = props.mode === 'kitchen' 
        ? order.cooking_duration 
        : order.serving_duration
      return TimeCalculator.formatDuration(duration)
    }
    
    const getBatchActionText = () => {
      const pendingCount = filteredOrders.value.filter(order => {
        if (props.mode === 'kitchen') {
          return order.dish_status === '待制作' || order.dish_status === '制作中'
        } else {
          return order.dish_status === '已制作待上菜'
        }
      }).length
      
      if (pendingCount === 0) {
        return props.mode === 'kitchen' ? '无待制作订单' : '无待上菜订单'
      }
      
      const actionText = props.mode === 'kitchen' ? '批量完成制作' : '批量完成上菜'
      return `${actionText} (${pendingCount})`
    }
    
    const handleOrderAction = async (order) => {
      if (order.processing) return
      
      // 设置处理状态
      order.processing = true
      
      try {
        if (props.mode === 'kitchen') {
          await ordersStore.completeCookingForOrder(order)
        } else {
          await deliveryStore.completeServingForOrder(order)
        }
        
        emit('order-action', { order, mode: props.mode })
        
        uni.showToast({
          title: props.mode === 'kitchen' ? '制作完成' : '上菜完成',
          icon: 'success',
          duration: 1500
        })
      } catch (error) {
        console.error('订单操作失败:', error)
        uni.showToast({
          title: error.message || '操作失败',
          icon: 'error',
          duration: 2000
        })
      } finally {
        order.processing = false
      }
    }
    
    const handleBatchAction = async () => {
      if (batchProcessing.value || !hasPendingOrders.value) return
      
      batchProcessing.value = true
      
      try {
        const pendingOrders = filteredOrders.value.filter(order => {
          if (props.mode === 'kitchen') {
            return order.dish_status === '待制作' || order.dish_status === '制作中'
          } else {
            return order.dish_status === '已制作待上菜'
          }
        })
        
        if (props.mode === 'kitchen') {
          await ordersStore.batchCompleteCooking(pendingOrders)
        } else {
          await deliveryStore.batchCompleteServing(pendingOrders)
        }
        
        emit('batch-action', { orders: pendingOrders, mode: props.mode })
        
        uni.showToast({
          title: `批量${props.mode === 'kitchen' ? '制作' : '上菜'}完成`,
          icon: 'success',
          duration: 1500
        })
        
        // 批量操作成功后可能需要关闭模态框
        setTimeout(() => {
          handleClose()
        }, 1500)
        
      } catch (error) {
        console.error('批量操作失败:', error)
        uni.showToast({
          title: error.message || '批量操作失败',
          icon: 'error',
          duration: 2000
        })
      } finally {
        batchProcessing.value = false
      }
    }
    
    // 监听器
    watch(() => props.visible, (newVal) => {
      if (newVal) {
        // 模态框打开时重置状态
        orderFilter.value = 'all'
        batchProcessing.value = false
      }
    })
    
    return {
      orderFilter,
      batchProcessing,
      stationName,
      priorityClass,
      priorityText,
      timeLabel,
      timeValue,
      timeClass,
      orderCount,
      filteredOrders,
      hasPendingOrders,
      handleMaskClick,
      handleClose,
      setOrderFilter,
      orderItemClass,
      getStatusClass,
      getStatusText,
      getDurationClass,
      formatOrderTime,
      formatDetailTime,
      formatOrderDuration,
      getBatchActionText,
      handleOrderAction,
      handleBatchAction
    }
  }
}
</script>

<style scoped>
.detail-modal {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
  padding: 40upx;
}

.modal-content {
  background: #ffffff;
  border-radius: 24upx;
  width: 100%;
  max-width: 800upx;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 模态框头部 */
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 32upx 32upx 24upx;
  border-bottom: 2upx solid #f0f0f0;
}

.modal-title {
  font-size: 36upx;
  font-weight: 600;
  color: #1a1a1a;
}

.close-btn {
  width: 48upx;
  height: 48upx;
  display: flex;
  justify-content: center;
  align-items: center;
  border-radius: 50%;
  background: #f5f5f5;
}

.close-icon {
  font-size: 36upx;
  color: #666666;
  line-height: 1;
}

/* 菜品摘要 */
.dish-summary {
  padding: 24upx 32upx;
  border-bottom: 2upx solid #f0f0f0;
}

.dish-title {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20upx;
}

.dish-name {
  font-size: 32upx;
  font-weight: 600;
  color: #1a1a1a;
  flex: 1;
}

.dish-tags {
  display: flex;
  gap: 12upx;
}

.station-tag {
  background: #e6f7ff;
  color: #1890ff;
  padding: 6upx 16upx;
  border-radius: 16upx;
  font-size: 24upx;
}

.priority-tag {
  padding: 6upx 16upx;
  border-radius: 16upx;
  font-size: 24upx;
  font-weight: 500;
}

.priority-tag.priority-normal {
  background: #f6ffed;
  color: #52c41a;
}

.priority-tag.priority-warning {
  background: #fffbe6;
  color: #faad14;
}

.priority-tag.priority-urgent {
  background: #fff2f0;
  color: #ff4d4f;
}

.dish-stats {
  display: flex;
  gap: 32upx;
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-label {
  font-size: 24upx;
  color: #666666;
  margin-bottom: 8upx;
}

.stat-value {
  font-size: 28upx;
  font-weight: 600;
  color: #1a1a1a;
}

.stat-value.pending {
  color: #faad14;
}

.stat-value.ready {
  color: #1890ff;
}

.stat-value.time-normal {
  color: #52c41a;
}

.stat-value.time-warning {
  color: #faad14;
}

.stat-value.time-urgent {
  color: #ff4d4f;
}

/* 订单列表区域 */
.orders-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24upx 32upx 16upx;
}

.section-title {
  font-size: 28upx;
  font-weight: 600;
  color: #1a1a1a;
}

.filter-buttons {
  display: flex;
  gap: 4upx;
  background: #f5f5f5;
  border-radius: 12upx;
  padding: 4upx;
}

.filter-btn {
  padding: 8upx 20upx;
  border-radius: 8upx;
  font-size: 24upx;
  color: #666666;
  transition: all 0.2s ease;
}

.filter-btn.active {
  background: #ffffff;
  color: #1890ff;
  font-weight: 500;
}

.orders-list {
  flex: 1;
  padding: 0 32upx 24upx;
}

/* 订单项 */
.order-item {
  background: #fafafa;
  border-radius: 16upx;
  padding: 20upx;
  margin-bottom: 16upx;
  border-left: 6upx solid #e5e5e5;
  transition: all 0.3s ease;
}

.order-item.time-normal {
  border-left-color: #52c41a;
}

.order-item.time-warning {
  border-left-color: #faad14;
  background: #fffef7;
}

.order-item.time-urgent {
  border-left-color: #ff4d4f;
  background: #fff5f5;
}

.order-item.processing {
  opacity: 0.7;
}

.order-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16upx;
}

.order-info {
  flex: 1;
}

.table-number {
  font-size: 32upx;
  font-weight: 600;
  color: #1a1a1a;
  margin-right: 16upx;
}

.order-time {
  font-size: 24upx;
  color: #666666;
}

.order-status {
  display: flex;
  align-items: center;
  gap: 12upx;
}

.quantity {
  font-size: 28upx;
  font-weight: 600;
  color: #1890ff;
}

.status-text {
  font-size: 24upx;
  padding: 4upx 12upx;
  border-radius: 12upx;
  font-weight: 500;
}

.status-text.status-pending {
  background: #fff7e6;
  color: #fa8c16;
}

.status-text.status-cooking {
  background: #e6f7ff;
  color: #1890ff;
}

.status-text.status-ready {
  background: #f6ffed;
  color: #52c41a;
}

.status-text.status-served {
  background: #f0f0f0;
  color: #8c8c8c;
}

.order-details {
  display: flex;
  flex-direction: column;
  gap: 8upx;
  margin-bottom: 16upx;
}

.time-info {
  display: flex;
  align-items: center;
}

.time-label {
  font-size: 24upx;
  color: #666666;
  width: 160upx;
  flex-shrink: 0;
}

.time-value {
  font-size: 24upx;
  color: #1a1a1a;
}

.duration-value {
  font-size: 24upx;
  font-weight: 500;
}

.duration-value.duration-normal {
  color: #52c41a;
}

.duration-value.duration-warning {
  color: #faad14;
}

.duration-value.duration-urgent {
  color: #ff4d4f;
}

.notes-value {
  font-size: 24upx;
  color: #1a1a1a;
  background: #f0f0f0;
  padding: 8upx 12upx;
  border-radius: 8upx;
  flex: 1;
}

.order-actions {
  display: flex;
  justify-content: flex-end;
}

.order-action-btn {
  background: #1890ff;
  color: #ffffff;
  border: none;
  border-radius: 8upx;
  padding: 12upx 24upx;
  font-size: 24upx;
  font-weight: 500;
}

.order-action-btn:active:not(.btn-disabled) {
  background: #096dd9;
}

.order-action-btn.btn-disabled {
  background: #d9d9d9;
  color: #999999;
}

.empty-orders {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200upx;
}

.empty-text {
  font-size: 28upx;
  color: #999999;
}

/* 底部按钮 */
.modal-footer {
  display: flex;
  gap: 16upx;
  padding: 24upx 32upx 32upx;
  border-top: 2upx solid #f0f0f0;
}

.footer-btn {
  flex: 1;
  height: 80upx;
  border-radius: 12upx;
  border: none;
  font-size: 28upx;
  font-weight: 600;
  transition: all 0.2s ease;
}

.secondary-btn {
  background: #f5f5f5;
  color: #666666;
}

.secondary-btn:active {
  background: #e6e6e6;
}

.primary-btn {
  background: #1890ff;
  color: #ffffff;
}

.primary-btn:active:not(.btn-disabled) {
  background: #096dd9;
}

.btn-disabled {
  background: #d9d9d9 !important;
  color: #999999 !important;
}

.btn-text {
  font-size: inherit;
  font-weight: inherit;
}
</style> 