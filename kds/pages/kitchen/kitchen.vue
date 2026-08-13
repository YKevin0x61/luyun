<template>
  <view class="kitchen-page">
    <!-- 屏幕状态边框：全屏 overlay，中心透明、不拦截点击；绿/黄闪/红/超时深红脉冲 -->
    <view
      class="screen-border"
      :class="'screen-border--' + screenBorderVisual"
      aria-hidden="true"
    />

    <!-- H5 一次性提示音解锁遮罩（APP-PLUS 无需） -->
    <view
      v-if="showSoundUnlockOverlay"
      class="sound-unlock-overlay"
      @click="unlockSoundFromGesture"
    >
      <view class="sound-unlock-card" @click.stop="unlockSoundFromGesture">
        <text class="sound-unlock-title">开启提示音</text>
        <text class="sound-unlock-hint">点击任意处解锁浏览器音频，避免漏听新单叮声</text>
        <button class="sound-unlock-btn" @click.stop="unlockSoundFromGesture">
          <text>开启提示音</text>
        </button>
      </view>
    </view>

    <!-- 整合的标题栏 -->
    <view class="integrated-header">
      <view class="header-content">
        <view class="header-left">
          <button @click="goBack" class="back-btn">
            <text class="back-icon">←</text>
            <text class="back-text">返回</text>
          </button>
          <text class="current-time current-time--full">{{ currentTime }}</text>
          <text class="current-time current-time--short">{{ currentTimeShort }}</text>
        </view>
        
        <view class="header-center">
          <text class="main-title">厨房控制台</text>
        </view>
        
        <view class="header-right">
          <view class="connection-status" :class="connectionStatusClass">
            <text>{{ connectionStatusText }}</text>
          </view>
          <button @click="refreshData" :disabled="loading" class="refresh-btn">
            <text>{{ loading ? '刷新中...' : '手动刷新' }}</text>
          </button>
        </view>
      </view>
    </view>

    <!-- 🆕 断连告警：WS 实时连接断开/重连中时展示醒目红色横条 -->
    <view v-if="showDisconnectBanner" class="disconnect-banner">
      <SvgIcon name="alert-triangle" :size="18" color="#fff" />
      <text class="disconnect-banner-text">实时连接已断开，正在重连…</text>
    </view>

    <!-- 🆕 打印失败提示条：出餐已成功，仅小票打印失败时展示，可手动补打 -->
    <view v-if="printFailedCount > 0" class="print-fail-banner">
      <view class="print-fail-info">
        <SvgIcon name="alert-triangle" :size="18" color="#fff" />
        <text class="print-fail-text">打印失败 {{ printFailedCount }} 张小票（出餐记录已保存，不受影响）</text>
      </view>
      <button @click="retryFailedPrints" class="print-retry-btn">
        <text>补打</text>
      </button>
    </view>

    <!-- 🆕 外卖取消告警：外卖单在 POS 被取消后展示醒目横条；含已出餐/已打票时高亮并加强提示音 -->
    <view v-if="deliveryCancelAlert.visible" :class="['cancel-banner', { cooked: deliveryCancelAlert.hasCooked }]">
      <view class="cancel-banner-info">
        <SvgIcon name="alert-triangle" :size="20" color="#fff" />
        <text class="cancel-banner-text">
          <text class="cancel-banner-title">{{ deliveryCancelAlert.hasCooked ? '⚠ 外卖已取消（含已出餐/已打票，请核对退单）' : '外卖订单已取消' }} {{ deliveryCancelAlert.count }} 项</text>
          <text class="cancel-banner-list">：{{ deliveryCancelAlert.summary }}</text>
        </text>
      </view>
      <button @click="dismissDeliveryCancelAlert" class="cancel-dismiss-btn">
        <text>知道了</text>
      </button>
    </view>

    <!-- 新单未确认：黄闪期间点「已知晓」停闪静音、清角标、边框转红 -->
    <view v-if="awaitingAck" class="ack-banner">
      <view class="ack-banner-info">
        <SvgIcon name="alert-triangle" :size="18" color="#fff" />
        <text class="ack-banner-text">有新单待确认，请查看后点击「已知晓」</text>
      </view>
      <button @click="acknowledgeNewOrders" class="ack-banner-btn">
        <text>已知晓</text>
      </button>
    </view>

    <!-- 合并的控制面板 - 横向布局 -->
    <view class="control-panel" :class="{ 'control-panel--no-tabs': isSingleWatchedStation }">
      <!-- 档口标签区域：职责集恰为 1 个时隐藏，锁定该档全屏 -->
      <view v-if="!isSingleWatchedStation" class="panel-section tabs-section">
        <scroll-view scroll-x class="tabs-scroll">
          <view class="tabs-container">
            <button
              v-for="station in stationTabs"
              :key="station.id"
              @click="switchStation(station.id)"
              :class="['station-tab', station.id, { active: currentStation === station.id }]"
              :style="{ borderColor: station.color }"
            >
              <view class="tab-content">
                <text class="tab-name">{{ station.name }}</text>
                <view class="tab-info">
                  <text class="tab-count">{{ stationTabStats[station.id]?.pending ?? 0 }}</text>
                  <text class="tab-unit">单</text>
                </view>
                <view v-if="(stationTabStats[station.id]?.urgent ?? 0) > 0" class="tab-urgent">
                  <text>{{ stationTabStats[station.id]?.urgent }}急</text>
                </view>
              </view>
            </button>
          </view>
        </scroll-view>
      </view>

      <!-- 档口信息区域 -->
      <view class="panel-section info-section">
        <!-- 统计信息 -->
        <view class="stats-mini-section">
          <view class="stat-mini-card urgent">
            <text class="stat-mini-value">{{ currentStationStats?.overtimeCount || 0 }}</text>
            <text class="stat-mini-title">超时</text>
          </view>
          <view class="stat-mini-card pending">
            <text class="stat-mini-value">{{ currentStationStats?.pendingCount || 0 }}</text>
            <text class="stat-mini-title">待制作</text>
          </view>
          <view class="stat-mini-card completed">
            <text class="stat-mini-value">{{ currentStationStats?.completedToday || 0 }}</text>
            <text class="stat-mini-title">已制作</text>
          </view>
          <view class="stat-mini-card efficiency">
            <text class="stat-mini-value">{{ currentStationStats?.avgCookingTime || '0分' }}</text>
            <text class="stat-mini-title">平均制作</text>
          </view>
        </view>
      </view>

      <!-- 排序选项区域 -->
      <view class="panel-section sort-section">
        <view class="sort-buttons">
          <button 
            v-for="option in sortOptions" 
            :key="option.value"
            @click="setSortBy(option.value)"
            :class="['sort-btn', { active: currentSort === option.value }]"
          >
            <text>{{ option.label }}</text>
          </button>
        </view>
      </view>
    </view>

    <!-- 订单列表 -->
    <view class="orders-section">
      <!-- <view class="section-header">
        <text class="section-title">待制作订单</text>
        <view class="orders-count">
          <text>共 {{ currentStationMergedDishes.length }} 个菜品</text>
        </view>
      </view> -->
      
      <scroll-view scroll-y class="orders-scroll">
        <view v-if="loading && currentStationMergedDishes.length === 0" class="loading-container">
          <text class="loading-text">加载中...</text>
        </view>
        <view v-else-if="currentStationMergedDishes.length === 0" class="empty-container">
          <view class="empty-text"><SvgIcon name="sparkles" :size="20" color="#22c55e" /><text>暂无待制作订单</text></view>
        </view>
        <view v-else class="orders-list" :class="'density-' + densityMode">
          <!-- H5 用 transition-group 做排序动画；非 H5 避免原生端标签不匹配，卡片模板共用 KitchenDishCard -->
          <view v-if="isH5" style="display: contents;">
            <transition-group
              name="dish-sort"
              tag="view"
              class="dishes-container"
            >
              <KitchenDishCard
                v-for="dish in currentStationMergedDishes"
                :key="dish.chunkId"
                :dish="dish"
                :density="densityMode"
                :selected-quantity="getDishSelectedQuantity(dish.chunkId)"
                :is-new="dishHasNewBadge(dish)"
                @increase="increaseQuantity(dish.chunkId, dish.totalQuantity)"
                @decrease="decreaseQuantity(dish.chunkId)"
                @show-detail="showDishDetail(dish)"
              />
            </transition-group>
          </view>
          <view v-else class="dishes-container">
            <KitchenDishCard
              v-for="dish in currentStationMergedDishes"
              :key="dish.chunkId"
              :dish="dish"
              :density="densityMode"
              :selected-quantity="getDishSelectedQuantity(dish.chunkId)"
              :is-new="dishHasNewBadge(dish)"
              @increase="increaseQuantity(dish.chunkId, dish.totalQuantity)"
              @decrease="decreaseQuantity(dish.chunkId)"
              @show-detail="showDishDetail(dish)"
            />
          </view>
        </view>
      </scroll-view>
    </view>

    <!-- 底部工具栏 -->
    <!-- <view class="toolbar">
      <view class="toolbar-left">
        <view class="station-status" :class="stationStatusClass">
          <text>档口状态: {{ stationStatusText }}</text>
        </view>
      </view>
      <view class="toolbar-right">
        <text class="auto-refresh-info">
          自动刷新: {{ autoRefreshInterval }}s | 成功率: {{ pollingSuccessRate }}%
        </text>
      </view>
    </view> -->

    <!-- 更新信息 -->
    <!-- <view class="update-info">
      <text>最后更新: {{ lastUpdateTime }}</text>
      <text>数据变化: {{ dataChangeIndicator }}</text>
    </view> -->
    
    <!-- 🆕 批量提交悬浮按钮 -->
    <view 
      v-if="totalSelectedCount > 0" 
      class="batch-submit-float"
    >
      <button 
        @click="batchSubmitCooking" 
        :disabled="batchSubmitting"
        class="batch-submit-btn"
      >
        <SvgIcon class="submit-icon" name="utensils" :size="18" color="#fff" />
        <text class="submit-text">
          {{ batchSubmitting ? '提交中...' : `提交出餐 (${totalSelectedCount})` }}
        </text>
      </button>
    </view>
    
    <!-- 🆕 菜品详情模态框 -->
    <view v-if="showDetailModal" class="modal-overlay" @click="closeDetailModal">
      <view class="modal-container" @click.stop>
        <view class="modal-header">
          <text class="modal-title">菜品详情</text>
          <button @click="closeDetailModal" class="modal-close">
            <SvgIcon name="x" :size="16" color="#666" />
          </button>
        </view>
        
        <view class="modal-content" v-if="currentDetailDish">
          <view class="detail-section">
            <text class="detail-label">菜品名称：</text>
            <text class="detail-value">{{ currentDetailDish.dishName }}</text>
          </view>
          
          <view class="detail-section">
            <text class="detail-label">总数量：</text>
            <text class="detail-value">{{ currentDetailDish.totalQuantity }}份</text>
          </view>
          
          <view class="detail-section">
            <text class="detail-label">订单数：</text>
            <text class="detail-value">{{ currentDetailDish.orders.length }}单</text>
          </view>
          
          <view class="detail-section">
            <text class="detail-label">等待时间：</text>
            <text class="detail-value" :class="currentDetailDish.waitTimeClass">
              {{ currentDetailDish.maxWaitTimeFormatted }}
            </text>
          </view>
          
          <view class="detail-section">
            <text class="detail-label">已选数量：</text>
            <text class="detail-value">{{ getDishSelectedQuantity(currentDetailDish.chunkId) }}份</text>
          </view>
          
          <view class="orders-detail-section">
            <text class="section-title">订单详情：</text>
            <view class="orders-list-modal">
              <view v-for="order in currentDetailDish.orders" :key="order.id" class="order-item">
                <view class="order-header">
                  <text class="order-table">{{ order.table_number }}桌</text>
                  <text class="order-quantity">{{ order.quantity }}份</text>
                </view>
                <text class="order-time">下单时间：{{ formatTime(order.order_time) }}</text>
              </view>
            </view>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>

<script>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { useOrdersStore } from '../../stores/orders.js'
import { useRealtimeStore } from '../../stores/realtime.js'
import { useStationsStore } from '../../stores/stations.js'
import { TimeCalculator } from '../../utils/timeCalculator.js'
import { isRefundOrder } from '../../utils/constants.js'
import { OrderPrioritySelector } from '../../utils/prioritySelector.js'
import { groupOrdersByDish } from '../../utils/dishMerge.js'
import { composeKitchenDishCards } from '../../utils/dishCardChunks.js'
import { enqueuePrintTicket, subscribeQueueState, retryAllFailedJobs } from '../../utils/printQueue.js'
import { debugLog } from '../../utils/debug.js'
import { planBatchCookingCalls } from '../../utils/batchCooking.js'
import { ScreenSettingsManager, DENSITY_MODES } from '../../utils/storage.js'
import { useKitchenOrderSession } from '../../composables/useKitchenOrderSession.js'
import { useDisconnectAlert } from '../../composables/useDisconnectAlert.js'
import { useNudgePull } from '../../composables/useNudgePull.js'
import { takeSettingsReturnClear } from '../../utils/kitchenSelectionReset.js'
import SvgIcon from '../../components/SvgIcon/SvgIcon.vue'
import KitchenDishCard from '../../components/KitchenDishCard/KitchenDishCard.vue'

// 各页独立订阅 id；后端按连接去重推送，卸载时可安全 unsubscribe
const ORDERS_SUBSCRIPTION_ID = 'kds-kitchen-orders'

export default {
  name: 'KitchenPage',
  components: { SvgIcon, KitchenDishCard },
  
  setup() {
    const ordersStore = useOrdersStore()
    const realtimeStore = useRealtimeStore()
    const stationsStore = useStationsStore()
    
    // 平台标识：是否为H5
    const isH5 = typeof window !== 'undefined' && !!window.document

    // 🆕 时钟 tick 间隔：仅用于驱动"已等待时长"等显示的每秒刷新，不驱动订单合并/统计聚合
    const CLOCK_TICK_INTERVAL_MS = 1000

    // 订单会话：一次拉取 → 新单/外卖取消引擎；职责档口 / 拆卡上限 / 紧急阈值随 refresh/onShow 重读
    const orderSession = useKitchenOrderSession({ ordersStore })
    const {
      loading,
      watchedStationIds,
      thresholdsMs,
      dishCardQuantityCap,
      refresh: refreshData,
      start: startKitchenOrderSession,
      stop: stopKitchenOrderSession,
      onShow: onKitchenOrderSessionShow,
      screenBorderVisual,
      awaitingAck,
      showSoundUnlockOverlay,
      acknowledgeNewOrders,
      dishHasNewBadge,
      unlockSoundFromGesture,
      deliveryCancelAlert,
      dismissDeliveryCancelAlert,
      decorateDishWait,
      currentStationStats: buildCurrentStationStats,
      urgentCutoff
    } = orderSession

    const {
      showDisconnectBanner,
      start: startDisconnectAlert,
      stop: stopDisconnectAlert
    } = useDisconnectAlert(() => realtimeStore.connectionStatus)

    // 响应式数据
    const operationLoading = ref(false)
    const currentTime = ref('')
    const currentTimeShort = ref('')
    const currentTimestamp = ref(Date.now()) // 新增：响应式时间戳
    const timeInterval = ref(null)
    const currentStation = ref('changfen') // 默认选中肠粉档
    const currentSort = ref('time')
    
    // 🆕 数量控制相关状态（key = chunkId；N=0 时 chunkId 即菜名）
    const selectedQuantities = ref({}) // 记录每个卡片选中的数量
    const batchSubmitting = ref(false) // 批量提交状态
    
    // 🆕 详情模态框状态
    const showDetailModal = ref(false)
    const currentDetailDish = ref(null)

    // 🆕 打印队列状态（失败可见 + 补打），由 printQueue 订阅回调驱动，非本页轮询
    const printFailedCount = ref(0)
    const printPendingCount = ref(0)
    let unsubscribePrintQueue = null

    // 显示密度（进页快照；设置页改完重进厨房页生效）
    const densityMode = ScreenSettingsManager.getDensity() || DENSITY_MODES.STANDARD

    // 档口标签：stationsStore 为源，再按职责集过滤
    const stationTabs = computed(() => {
      const all = stationsStore.stationList.map(({ id, name, color }) => ({ id, name, color }))
      const watchedIds = watchedStationIds.value
      if (!watchedIds.length) return all
      const watched = new Set(watchedIds)
      return all.filter((station) => watched.has(station.id))
    })

    // 职责集恰为 1 个：锁定该档全屏、隐藏 Tab 栏（按配置集大小，非过滤后可见数）
    const isSingleWatchedStation = computed(() => watchedStationIds.value.length === 1)
    
    // 排序选项
    const sortOptions = ref([
      { value: 'time', label: '按时间' },
      { value: 'quantity', label: '按数量' },
      { value: 'urgency', label: '按紧急度' }
    ])

    // 先按逻辑菜排序（N>0 时同名拆卡仍相邻）；时间序用 oldestTimestamp，不依赖每秒时钟
    const sortMergedDishes = (dishes) => {
      return [...dishes].sort((a, b) => {
        switch (currentSort.value) {
          case 'time':
            return a.oldestTimestamp - b.oldestTimestamp
          case 'quantity':
            return b.totalQuantity - a.totalQuantity
          case 'urgency':
            const priorityA = OrderPrioritySelector.calculatePriority(new Date(Math.min(...a.orders.map(o => new Date(o.order_time)))))
            const priorityB = OrderPrioritySelector.calculatePriority(new Date(Math.min(...b.orders.map(o => new Date(o.order_time)))))
            const weights = { urgent: 3, high: 2, normal: 1 }
            return (weights[priorityB] || 1) - (weights[priorityA] || 1)
          default:
            return 0
        }
      }).map(dish => {
        return {
          ...dish,
          orders: [...dish.orders].sort((a, b) => {
            return new Date(a.order_time) - new Date(b.order_time)
          })
        }
      })
    }
    
    // 计算属性
    const currentStationInfo = computed(() => {
      return stationTabs.value.find(station => station.id === currentStation.value) || {}
    })
    
    const isPendingCookOrder = (order) =>
      order && order.dish_status === '待出餐' && !isRefundOrder(order)

    // 🔧 性能优化：二分查找统计有序时间戳数组中"早于等于 cutoff"的数量（即等待超过阈值的订单数）
    // O(log n)，供每秒 tick 时使用，避免重新遍历+解析全量订单时间
    const countTimestampsAtOrBefore = (sortedTimestamps, cutoff) => {
      let lo = 0
      let hi = sortedTimestamps.length
      while (lo < hi) {
        const mid = (lo + hi) >>> 1
        if (sortedTimestamps[mid] <= cutoff) {
          lo = mid + 1
        } else {
          hi = mid
        }
      }
      return lo
    }

    // 🔧 性能优化：数据类计算，只依赖订单数据本身（ordersStore.orders 变化时才重算）
    // 按档口预聚合待出餐订单数量，并将下单时间戳升序排序，供 stationTabStats 每秒 tick 时
    // 用二分查找快速统计超时数量，不再每秒对全量订单做 Date 解析与遍历
    const pendingTimestampsByStation = computed(() => {
      const result = {}
      const orders = ordersStore.orders

      if (!Array.isArray(orders)) return result

      for (const order of orders) {
        if (!isPendingCookOrder(order)) continue

        const stationId = order.station
        if (!stationId) continue

        if (!result[stationId]) {
          result[stationId] = { pending: 0, timestamps: [] }
        }

        result[stationId].pending++

        if (order.order_time) {
          const orderTime = new Date(order.order_time).getTime()
          if (!Number.isNaN(orderTime)) {
            result[stationId].timestamps.push(orderTime)
          }
        }
      }

      Object.values(result).forEach(entry => entry.timestamps.sort((a, b) => a - b))

      return result
    })

    // 🔧 性能优化：轻量计算，仅对 pendingTimestampsByStation 预排序的时间戳做二分统计，
    // 不重新扫描/解析全量订单，每秒 tick 的开销从 O(全量订单) 降为 O(档口数 × log(该档口订单数))
    const stationTabStats = computed(() => {
      const stats = {}
      const now = currentTimestamp.value
      const base = pendingTimestampsByStation.value
      const cutoff = urgentCutoff(now)
      // touch thresholdsMs so threshold edits after onShow/refresh recompute urgently
      void thresholdsMs.value.urgent

      for (const stationId of Object.keys(base)) {
        const { pending, timestamps } = base[stationId]
        stats[stationId] = {
          pending,
          urgent: countTimestampsAtOrBefore(timestamps, cutoff)
        }
      }

      return stats
    })

    // 获取当前档口的订单
    const currentStationOrders = computed(() => {
      return ordersStore.getOrdersByStation(currentStation.value).filter(isPendingCookOrder)
    })
    
    // 🔧 性能优化：数据类计算，只依赖订单数据本身（currentStationOrders 变化时才重算）
    // 按菜品名称分组、解析下单时间戳得到"最早下单时间"；不引用 currentTimestamp，
    // 不随每秒时钟重算，只在订单数据变化（下单/出餐/切换档口）时重新分组
    const currentStationMergedDishesBase = computed(() => {
      // 🔧 优化：只在订单数量变化时输出调试信息
      const orderCount = currentStationOrders.value.length
      // 🔧 修复：检查 currentStation.value 是否有效，防止 undefined 访问错误
      const currentStationId = currentStation.value
      if (currentStationId && typeof currentStationId === 'string') {
        // 🔧 修复：安全初始化跨端可用的全局对象属性
        if (!globalThis.lastOrderCount) {
          globalThis.lastOrderCount = {}
        }
        
        const lastCount = globalThis.lastOrderCount[currentStationId]
        if (typeof lastCount === 'undefined' || lastCount !== orderCount) {
          debugLog(`[厨房合并] 档口${currentStationId}: 待出餐订单${orderCount}个`)
          if (orderCount > 0) {
            debugLog(`[厨房合并] 订单样本:`, currentStationOrders.value[0])
          }
          
          // 记录当前数量，避免重复输出
          globalThis.lastOrderCount[currentStationId] = orderCount
        }
      }
      
      // 按菜品名称分组（过滤无效订单，校验+日志与合并前保持一致）
      const validOrders = currentStationOrders.value.filter(order => {
        if (!order || !order.dish_name) {
          debugLog('[厨房合并] 订单数据无效:', order)
          return false
        }
        return true
      })
      const dishGroups = groupOrdersByDish(validOrders, order => order.dish_name)
      
      // 转换为数组，计算最早下单时间戳（时长/超时等派生字段延后到 currentStationMergedDishes 计算）
      return Object.values(dishGroups).map(dish => {
        // 🔧 确保菜品数据有效
        if (!dish || !dish.orders || dish.orders.length === 0) {
          debugLog('[厨房合并] 菜品数据无效:', dish)
          return null
        }
        
        const orderTimestamps = dish.orders
          .map(order => {
            if (!order || !order.order_time) {
              debugLog('[厨房合并] 订单时间无效:', order)
              return null
            }
            const ts = new Date(order.order_time).getTime()
            return Number.isNaN(ts) ? null : ts
          })
          .filter(ts => ts !== null)

        const oldestTimestamp = orderTimestamps.length > 0
          ? Math.min(...orderTimestamps)
          : Date.now()
        
        return {
          ...dish,
          oldestTimestamp,
          // 🔧 确保 orders 始终是数组
          orders: dish.orders || []
        }
      }).filter(dish => dish !== null) // 过滤掉无效的菜品
    })

    // Sticky FIFO 拆卡：cap 变化时丢弃上一轮快照（新 N 重新 FIFO）；成员身份跨 refresh 稳住
    const dishChunkSnapshotByDish = ref({})
    const dishChunkCapSeen = ref(null)
    const chunkedDishesBase = ref([])

    watch(
      [currentStationMergedDishesBase, dishCardQuantityCap, currentSort],
      () => {
        const cap = Number(dishCardQuantityCap.value) || 0
        if (dishChunkCapSeen.value !== null && dishChunkCapSeen.value !== cap) {
          selectedQuantities.value = {}
        }
        const previous =
          dishChunkCapSeen.value === cap ? dishChunkSnapshotByDish.value : {}
        dishChunkCapSeen.value = cap
        const logical = sortMergedDishes(currentStationMergedDishesBase.value)
        const { cards, previousByDish } = composeKitchenDishCards({
          logicalDishes: logical,
          cap,
          previousByDish: previous
        })
        dishChunkSnapshotByDish.value = previousByDish
        chunkedDishesBase.value = cards
      },
      { immediate: true }
    )

    // 每秒只给已拆好的卡片换算等待/超时（按该卡自己的订单）
    const currentStationMergedDishes = computed(() => {
      const now = currentTimestamp.value
      void thresholdsMs.value

      return chunkedDishesBase.value.map(dish => {
        const wait = decorateDishWait(dish.oldestTimestamp, now)
        return {
          ...dish,
          maxWaitTime: wait.maxWaitTime,
          maxWaitTimeFormatted: TimeCalculator.formatDurationClock(wait.maxWaitTime),
          isOvertime: wait.isOvertime,
          waitTimeClass: wait.waitTimeClass
        }
      })
    })
    
    // 🆕 总选中数量计算
    const totalSelectedCount = computed(() => {
      return Object.values(selectedQuantities.value).reduce((sum, qty) => sum + qty, 0)
    })
    
    // 🆕 选中的菜品列表
    const selectedDishes = computed(() => {
      return Object.keys(selectedQuantities.value).filter(chunkId => 
        selectedQuantities.value[chunkId] > 0
      ).map(chunkId => {
        const dish = currentStationMergedDishes.value.find(d => d.chunkId === chunkId)
        if (!dish) {
          debugLog(`[厨房页面] 找不到卡片: ${chunkId}`)
          return null
        }
        return {
          ...dish,
          selectedQuantity: selectedQuantities.value[chunkId],
          // 🔧 确保 orders 属性存在且为数组
          orders: dish.orders || []
        }
      }).filter(dish => dish !== null) // 过滤掉无效的菜品
    })
    
    // 当前档口统计（紧急阈值来自 orderSession / 本屏本地配置）
    const currentStationStats = computed(() => {
      void thresholdsMs.value.urgent
      if (!ordersStore || !currentStation.value) {
        return buildCurrentStationStats([], isPendingCookOrder)
      }
      const stationOrders = ordersStore.getOrdersByStation(currentStation.value)
      return buildCurrentStationStats(stationOrders, isPendingCookOrder)
    })
    
    // WebSocket连接状态
    const connectionStatusClass = computed(() => {
      return realtimeStore.connectionStatus === 'connected' ? 'connected' : 'disconnected'
    })
    
    const connectionStatusText = computed(() => {
      const statusMap = {
        'connected': '已连接',
        'disconnected': '已断开',
        'reconnecting': '重连中'
      }
      return statusMap[realtimeStore.connectionStatus] || '未知状态'
    })

    // 获取档口订单数量
    const getStationOrderCount = (stationId) => stationTabStats.value[stationId]?.pending ?? 0

    // 获取档口紧急订单数量
    const getStationUrgentCount = (stationId) => stationTabStats.value[stationId]?.urgent ?? 0
    
    // 档口状态 - 已注释掉状态栏相关的计算属性
    // const stationStatusClass = computed(() => {
    //   const urgentCount = getStationUrgentCount(currentStation.value)
    //   const pendingCount = getStationOrderCount(currentStation.value)
    //   
    //   if (urgentCount > 0) return 'urgent'
    //   if (pendingCount > 10) return 'busy'
    //   if (pendingCount > 5) return 'normal'
    //   return 'idle'
    // })
    
    // const stationStatusText = computed(() => {
    //   const urgentCount = getStationUrgentCount(currentStation.value)
    //   const pendingCount = getStationOrderCount(currentStation.value)
    //   
    //   if (urgentCount > 0) return `紧急 (${urgentCount}单超时)`
    //   if (pendingCount > 10) return `繁忙 (${pendingCount}单待处理)`
    //   if (pendingCount > 5) return `正常 (${pendingCount}单待处理)`
    //   return '空闲'
    // })
    
    // 轮询相关计算属性 - 已注释掉状态栏相关的计算属性
    // const autoRefreshInterval = computed(() => pollingStore.currentInterval / 1000)
    // const pollingSuccessRate = computed(() => 
    //   Math.round(pollingStore.successRate * 100)
    // )
    // const lastUpdateTime = computed(() => 
    //   pollingStore.lastUpdateTime ? TimeCalculator.formatTime(pollingStore.lastUpdateTime) : '未更新'
    // )
    // const dataChangeIndicator = computed(() => 
    //   pollingStore.hasDataChanged ? '🔄 有更新' : '✅ 无变化'
    // )
    
    // 时间格式化 - 只显示时分
    const formatTime = (timeStr) => {
      const date = new Date(timeStr)
      const hours = date.getHours().toString().padStart(2, '0')
      const minutes = date.getMinutes().toString().padStart(2, '0')
      return `${hours}:${minutes}`
    }
    
    // 更新当前时间和响应式时间戳
    const updateCurrentTime = () => {
      const now = new Date()
      const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
      const year = now.getFullYear()
      const month = String(now.getMonth() + 1).padStart(2, '0')
      const day = String(now.getDate()).padStart(2, '0')
      const hours = String(now.getHours()).padStart(2, '0')
      const minutes = String(now.getMinutes()).padStart(2, '0')
      const seconds = String(now.getSeconds()).padStart(2, '0')
      const weekday = weekdays[now.getDay()]
      
              currentTime.value = `${year}-${month}-${day} ${weekday} ${hours}:${minutes}:${seconds}`
        currentTimeShort.value = `${hours}:${minutes}:${seconds}`
        
        // 🆕 每秒更新时间戳，实现等待时间动态显示
        const currentSecond = now.getTime()
        currentTimestamp.value = currentSecond
    }
    
    // 方法
    // 🆕 ordersStore.orders 始终持有"当天全档口"订单（不再按档口过滤请求），
    // 切换档口只是本地重新按 currentStation 过滤展示，无需任何网络请求。
    const switchStation = (stationId) => {
      currentStation.value = stationId
      stationsStore.setCurrentStation(stationId)
      
      // 🆕 切换档口时清空选中数量与拆卡快照（档口订单集不同，不能沿用上一档的 chunk）
      selectedQuantities.value = {}
      dishChunkSnapshotByDish.value = {}
      dishChunkCapSeen.value = null
      debugLog(`[厨房页面] 切换到档口: ${stationId}, 已清空选中数量`)
    }

    // 当前选中档口不在职责集时，切到第一个职责档口；单档口配置则强制锁定
    const ensureCurrentStationInWatched = () => {
      const watchedIds = watchedStationIds.value
      if (watchedIds.length === 1) {
        switchStation(watchedIds[0])
        return
      }
      const tabs = stationTabs.value
      if (!tabs.length) return
      const stillValid = tabs.some((station) => station.id === currentStation.value)
      if (!stillValid) {
        switchStation(tabs[0].id)
      }
    }
    
    const setSortBy = (sortType) => {
      currentSort.value = sortType
    }

    const refreshDataWithToast = async () => {
      try {
        debugLog('[厨房页面] 开始刷新当天数据...')
        await refreshData()
        ensureCurrentStationInWatched()
        debugLog('[厨房页面] 当天数据刷新完成')
      } catch (error) {
        console.error('[厨房页面] 刷新数据失败:', error)
        uni.showToast({
          title: '刷新失败',
          icon: 'error'
        })
      }
    }
    
    const goBack = () => {
      uni.reLaunch({
        url: '/pages/index/index'
      })
    }

    // 🔧 出餐完成后把打印任务入队，交由 printQueue 串行处理+失败重试，不 await、不阻塞出餐主流程
    const tryPrintCompletedDish = (order, dishName, readyTime) => {
      enqueuePrintTicket({
        tableNumber: order.table_number,
        dishName,
        orderTime: order.order_time,
        readyTime,
        notes: order.notes
      })
      debugLog('[厨房页面] 出单打印已入队:', dishName, order.table_number)
    }

    // 🆕 手动补打：把当前所有失败任务重新排队
    const retryFailedPrints = () => {
      const count = retryAllFailedJobs()
      if (count > 0) {
        uni.showToast({ title: `已重新排队 ${count} 张`, icon: 'none' })
      }
    }
    

    // 🆕 数量控制方法（key = chunkId）
    const increaseQuantity = (chunkId, maxQuantity) => {
      if (!selectedQuantities.value[chunkId]) {
        selectedQuantities.value[chunkId] = 0
      }
      if (selectedQuantities.value[chunkId] < maxQuantity) {
        selectedQuantities.value[chunkId]++
      }
    }
    
    const decreaseQuantity = (chunkId) => {
      if (selectedQuantities.value[chunkId] && selectedQuantities.value[chunkId] > 0) {
        selectedQuantities.value[chunkId]--
        if (selectedQuantities.value[chunkId] === 0) {
          delete selectedQuantities.value[chunkId]
        }
      }
    }
    
    // 🆕 获取卡片当前选中数量
    const getDishSelectedQuantity = (chunkId) => {
      return selectedQuantities.value[chunkId] || 0
    }
    
    // 🆕 显示菜品详情
    const showDishDetail = (dish) => {
      currentDetailDish.value = dish
      showDetailModal.value = true
    }
    
    // 🆕 关闭详情模态框
    const closeDetailModal = () => {
      showDetailModal.value = false
      currentDetailDish.value = null
    }
    
    // 批量提交出餐：先规划目标订单集合，再按菜品合并调用（消除逐单串行重查）
    const batchSubmitCooking = async () => {
      if (batchSubmitting.value || totalSelectedCount.value === 0) return

      const totalCount = totalSelectedCount.value
      batchSubmitting.value = true
      let actualProcessedCount = 0

      try {
        const isSubmittable = (order) =>
          isPendingCookOrder(order) && TimeCalculator.isToday(order.order_time)

        const pendingOrders = ordersStore.getOrdersByStation(currentStation.value)
          .filter(isSubmittable)

        const chunkOrders = {}
        for (const dish of selectedDishes.value) {
          chunkOrders[dish.chunkId] = {
            dishName: dish.dishName,
            orders: (dish.orders || []).filter(isSubmittable)
          }
        }

        const plan = planBatchCookingCalls({
          selectedQuantities: selectedQuantities.value,
          pendingOrders,
          chunkOrders
        })

        if (plan.length === 0) {
          uni.showToast({ title: '没有可提交的订单', icon: 'none' })
          return
        }

        const readyTime = new Date().toISOString()

        for (const item of plan) {
          const completeData = {
            dishName: item.dishName,
            station: currentStation.value,
            completeQuantity: item.completeQuantity,
            orders: item.orders,
            operatorId: 'chef_' + currentStation.value,
            notes: `${currentStationInfo.value.name}批量制作完成`,
            ready_time: readyTime
          }

          await ordersStore.completeCooking(completeData)

          for (const { order, serveQuantity } of item.allocations) {
            for (let i = 0; i < serveQuantity; i++) {
              tryPrintCompletedDish(order, item.dishName, readyTime)
            }
          }
          actualProcessedCount += item.completeQuantity
        }

        selectedQuantities.value = {}

        if (actualProcessedCount === 0) {
          uni.showToast({ title: '没有可提交的订单', icon: 'none' })
        } else if (actualProcessedCount < totalCount) {
          uni.showToast({
            title: `部分出餐成功 ${actualProcessedCount}/${totalCount}份`,
            icon: 'none'
          })
        } else {
          uni.showToast({
            title: `批量出餐成功，共${actualProcessedCount}份`,
            icon: 'success'
          })
        }

        await refreshData()

      } catch (error) {
        console.error('批量出餐失败:', error)
        uni.showToast({
          title: '批量出餐失败: ' + (error.message || '未知错误'),
          icon: 'error'
        })
        if (actualProcessedCount > 0) {
          await refreshData()
        }
      } finally {
        batchSubmitting.value = false
      }
    }
    
    const completeCooking = async (dish) => {
      if (operationLoading.value) return

      operationLoading.value = true
      try {
        // 数据完整性检查和修复
        const dishName = dish.dishName || dish.name || '未知菜品'
        const totalQuantity = dish.totalQuantity || dish.total_quantity || dish.quantity || 1
        const orders = dish.orders || []
        
        const validOrders = orders.filter(order =>
          isPendingCookOrder(order) && TimeCalculator.isToday(order.order_time)
        )
        
        if (validOrders.length === 0) {
          uni.showToast({
            title: '没有当天的订单可操作',
            icon: 'none'
          })
          return
        }
        
        debugLog(`[厨房页面] 制作完成操作: ${dishName}, 当天有效订单: ${validOrders.length}个`)
        
        // 🔍 验证菜品名称与订单中的菜品名称是否匹配
        const dishNameMismatches = validOrders.filter(order => order.dish_name !== dishName)
        if (dishNameMismatches.length > 0) {
          debugLog('[厨房页面] 发现菜品名称不匹配的订单:', dishNameMismatches.map(order => ({
            orderId: order._id || order.id,
            orderDishName: order.dish_name,
            mergeDishName: dishName,
            equal: order.dish_name === dishName
          })))
        }
        
        // 🔍 打印第一个订单的详细信息用于诊断
        if (validOrders.length > 0) {
          const firstOrder = validOrders[0]
          debugLog('[厨房页面] 第一个订单详细信息:', {
            id: firstOrder._id || firstOrder.id,
            dish_name: firstOrder.dish_name,
            dish_status: firstOrder.dish_status,
            table_number: firstOrder.table_number,
            quantity: firstOrder.quantity,
            served_quantity: firstOrder.served_quantity || 0
          })
        }
        
        // 🆕 记录制作完成时间，确保时间记录准确
        const readyTime = new Date().toISOString()
        debugLog(`[厨房页面] 制作完成时间: ${readyTime}`)
        
        const completeData = {
          dishName: dishName,
          station: currentStation.value,
          completeQuantity: 1, // 默认只完成1份（最早的一个订单）
          orders: validOrders, // 只传递当天的订单
          operatorId: 'chef_' + currentStation.value,
          notes: `${currentStationInfo.value.name}制作完成`,
          ready_time: readyTime // 🆕 明确传递制作完成时间
        }
        
        const response = await ordersStore.completeCooking(completeData)

        const completedOrder = validOrders[0]
        if (completedOrder) {
          tryPrintCompletedDish(completedOrder, dishName, readyTime)
        }
        
        uni.showToast({
          title: '最早一单制作完成',
          icon: 'success'
        })
        
        // 立即刷新数据
        debugLog('[厨房页面] 制作完成成功，立即刷新数据...')
        await refreshData()
        debugLog('[厨房页面] 数据刷新完成')
        
      } catch (error) {
        console.error('制作完成操作失败:', error)
        uni.showToast({
          title: '操作失败: ' + (error.message || '未知错误'),
          icon: 'error'
        })
      } finally {
        operationLoading.value = false
      }
    }
    
    // 首页档口行带入的 ?station=；大 CTA 不带参时为空
    const pendingStationFromQuery = ref('')
    onLoad((options) => {
      const station = options?.station
      if (typeof station === 'string' && station.trim()) {
        try {
          pendingStationFromQuery.value = decodeURIComponent(station.trim())
        } catch {
          pendingStationFromQuery.value = station.trim()
        }
      }
    })

    // 实时：nudge → 重拉当天订单并做外卖取消检测；60s reconcile 由 useNudgePull 默认 fallback
    const todayDateStr = TimeCalculator.formatTime(new Date(), 'YYYY-MM-DD')
    useNudgePull({
      id: ORDERS_SUBSCRIPTION_ID,
      topics: ['orders'],
      filters: { date: todayDateStr },
      pull: async () => {
        debugLog('[厨房页面] 收到 orders 实时通知，重拉当天数据...')
        await refreshDataWithToast()
      },
      fallback: 'reconcile',
    })

    // Settings return does not remount; clear only after a 系统设置 visit (not every onShow).
    onShow(() => {
      onKitchenOrderSessionShow()
      if (takeSettingsReturnClear()) {
        selectedQuantities.value = {}
      }
      ensureCurrentStationInWatched()
    })

    // 生命周期
    onMounted(async () => {
      debugLog('[厨房页面] 页面加载，强制获取当天数据')
      
      // 初始化档口
      await stationsStore.initializeStations()
      startKitchenOrderSession()
      startDisconnectAlert()

      const requestedStation = pendingStationFromQuery.value
      if (
        requestedStation &&
        ScreenSettingsManager.isStationWatched(requestedStation) &&
        stationTabs.value.some((tab) => tab.id === requestedStation)
      ) {
        switchStation(requestedStation)
      }
      ensureCurrentStationInWatched()

      // 初始加载：一次拉取并同步新单告警与外卖取消基线
      await refreshDataWithToast()

      // 开始时间更新
      updateCurrentTime()
      timeInterval.value = setInterval(updateCurrentTime, CLOCK_TICK_INTERVAL_MS)

      // 🆕 订阅打印队列状态，驱动"打印失败"提示条与补打角标
      unsubscribePrintQueue = subscribeQueueState((snapshot) => {
        printFailedCount.value = snapshot.failedCount
        printPendingCount.value = snapshot.pendingCount
      })
    })
    
    onUnmounted(() => {
      stopDisconnectAlert()
      
      // 清除时间更新
      if (timeInterval.value) {
        clearInterval(timeInterval.value)
      }

      stopKitchenOrderSession()

      // 🆕 取消打印队列状态订阅（队列本身是跨页面的全局单例，继续在后台处理，不随本页卸载而清理）
      if (unsubscribePrintQueue) {
        unsubscribePrintQueue()
        unsubscribePrintQueue = null
      }
    })
    
    return {
      isH5,
      // 响应式数据
      loading,
      operationLoading,
      currentTime,
      currentTimeShort,
      currentTimestamp,
      currentStation,
      currentSort,
      stationTabs,
      isSingleWatchedStation,
      densityMode,
      /** 本屏菜品卡片份数上限；0 = 不拆分（列表行为与今日一致） */
      dishCardQuantityCap,
      sortOptions,
      selectedQuantities,
      batchSubmitting,
      printFailedCount,
      printPendingCount,
      deliveryCancelAlert,
      dismissDeliveryCancelAlert,
      screenBorderVisual,
      awaitingAck,
      showSoundUnlockOverlay,
      acknowledgeNewOrders,
      dishHasNewBadge,
      unlockSoundFromGesture,
      
      // 计算属性
      currentStationInfo,
      currentStationMergedDishes,
      currentStationStats,
      connectionStatusClass,
      connectionStatusText,
      showDisconnectBanner,
      totalSelectedCount,
      selectedDishes,
      stationTabStats,
      // stationStatusClass,
      // stationStatusText,
      // autoRefreshInterval,
      // pollingSuccessRate,
      // lastUpdateTime,
      // dataChangeIndicator,
      
      // 方法
      switchStation,
      setSortBy,
      refreshData: refreshDataWithToast,
      goBack,
      getStationOrderCount,
      getStationUrgentCount,
      completeCooking,
      formatTime,
      // 🆕 数量控制方法
      increaseQuantity,
      decreaseQuantity,
      getDishSelectedQuantity,
      batchSubmitCooking,
      retryFailedPrints,
      // 🆕 详情模态框方法
      showDishDetail,
      closeDetailModal,
      showDetailModal,
      currentDetailDish
    }
  }
}
</script>

<style scoped>
.kitchen-page {
  min-height: 100vh;
  width: 100%;
  max-width: 100%;
  background: linear-gradient(135deg, #F5F7FA 0%, #E8EBF0 100%);
  display: flex;
  flex-direction: column;
  overflow-x: hidden;
  position: relative;
  padding-left: env(safe-area-inset-left);
  padding-right: env(safe-area-inset-right);
  touch-action: pan-y;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  box-sizing: border-box;
  scroll-behavior: smooth;
}

/* 屏幕状态边框：四周描边，中心透明，不挡操作 */
.screen-border {
  position: fixed;
  inset: 0;
  z-index: 9000;
  pointer-events: none;
  box-sizing: border-box;
  border-style: solid;
  border-width: 10upx;
  border-color: #52c41a;
  transition: border-color 0.2s ease;
}

.screen-border--green {
  border-color: #52c41a;
}

.screen-border--yellow {
  border-color: #faad14;
  animation: screen-border-yellow-flash 0.9s ease-in-out infinite;
}

.screen-border--red {
  border-color: #ff4d4f;
}

/* Overtime: deep-red strong pulse; engine already ranks this above yellow */
.screen-border--overtime {
  border-color: #820014;
  border-width: 14upx;
  animation: screen-border-overtime-pulse 0.7s ease-in-out infinite;
}

@keyframes screen-border-yellow-flash {
  0%,
  100% {
    border-color: #faad14;
    opacity: 1;
  }
  50% {
    border-color: #ffd666;
    opacity: 0.55;
  }
}

@keyframes screen-border-overtime-pulse {
  0%,
  100% {
    border-color: #820014;
    opacity: 1;
  }
  50% {
    border-color: #cf1322;
    opacity: 0.35;
  }
}

/* H5 提示音解锁遮罩 */
.sound-unlock-overlay {
  position: fixed;
  inset: 0;
  z-index: 9500;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.72);
  padding: 40upx;
  box-sizing: border-box;
}

.sound-unlock-card {
  width: min(640upx, 100%);
  background: #fff;
  border-radius: 20upx;
  padding: 48upx 40upx;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20upx;
  box-shadow: 0 16upx 48upx rgba(0, 0, 0, 0.25);
}

.sound-unlock-title {
  font-size: 36upx;
  font-weight: 800;
  color: #1f2937;
}

.sound-unlock-hint {
  font-size: 26upx;
  color: #64748b;
  text-align: center;
  line-height: 1.5;
}

.sound-unlock-btn {
  margin-top: 12upx;
  padding: 16upx 40upx;
  background: linear-gradient(135deg, #1890ff, #40a9ff);
  color: #fff;
  border: none;
  border-radius: 16upx;
  font-size: 28upx;
  font-weight: 700;
}

.sound-unlock-btn:active {
  transform: scale(0.96);
}

.ack-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16upx;
  padding: 14upx 20upx;
  background: linear-gradient(90deg, #d48806, #faad14);
  flex-shrink: 0;
  z-index: 20;
}

.ack-banner-info {
  display: flex;
  align-items: center;
  gap: 10upx;
  min-width: 0;
}

.ack-banner-text {
  color: #fff;
  font-size: 26upx;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ack-banner-btn {
  flex-shrink: 0;
  padding: 8upx 20upx;
  background: rgba(255, 255, 255, 0.25);
  border: 2upx solid rgba(255, 255, 255, 0.55);
  border-radius: 16upx;
  color: #fff;
  font-size: 24upx;
  font-weight: 700;
  touch-action: manipulation;
}

.ack-banner-btn:active {
  background: rgba(255, 255, 255, 0.4);
  transform: scale(0.95);
}

/* 全局box-sizing设置，确保所有元素适应屏幕宽度 */
.kitchen-page *,
.kitchen-page *::before,
.kitchen-page *::after {
  box-sizing: border-box;
}

/* 整合的标题栏 */
.integrated-header {
  flex-shrink: 0;
  width: 100%;
  background: linear-gradient(135deg, #4A90E2 0%, #357ABD 100%);
  box-shadow: 0 4upx 16upx rgba(0,0,0,0.15);
  padding-top: calc(env(safe-area-inset-top) + 16upx);
  color: white;
}

.header-content {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  column-gap: 16upx;
  width: 100%;
  padding: 16upx 20upx;
}

.header-left {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  gap: clamp(8upx, 2vw, 16upx);
  min-width: 0;
  justify-self: start;
  overflow: hidden;
}

.header-center {
  display: flex;
  justify-content: center;
  align-items: center;
  justify-self: center;
  white-space: nowrap;
  flex-shrink: 0;
  padding: 0 8upx;
}

.main-title {
  font-size: 36upx;
  font-weight: bold;
  color: white;
  text-shadow: 0 2upx 6upx rgba(0,0,0,0.3);
  letter-spacing: 1upx;
  white-space: nowrap;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 8upx;
  padding: 12upx 20upx;
  background: rgba(255, 255, 255, 0.2);
  border: 2upx solid rgba(255, 255, 255, 0.3);
  border-radius: 24upx;
  backdrop-filter: blur(10upx);
  transition: all 0.3s ease;
  box-shadow: 0 2upx 8upx rgba(0,0,0,0.1);
  flex-shrink: 0;
  /* 增强触摸友好性 */
  min-height: 44px; /* iOS 推荐的最小触摸目标 */
  min-width: 44px;
  touch-action: manipulation; /* 移除双击缩放延迟 */
}

.back-btn:active {
  background: rgba(255, 255, 255, 0.3);
  transform: scale(0.95);
  box-shadow: 0 1upx 4upx rgba(0,0,0,0.15);
}

.back-icon {
  font-size: 32upx;
  color: white;
  font-weight: bold;
  line-height: 1;
}

.back-text {
  font-size: 26upx;
  color: white;
  font-weight: 600;
  white-space: nowrap;
}

.connection-status {
  padding: 12upx 20upx; /* 减少内边距 */
  border-radius: 20upx; /* 减少圆角 */
  font-size: 22upx; /* 减少字体大小 */
  font-weight: 600;
  white-space: nowrap;
  min-width: 80upx; /* 减少最小宽度 */
  text-align: center;
  flex-shrink: 0;
  height: 44upx; /* 减少高度 */
  display: flex;
  align-items: center;
  justify-content: center;
}

.connection-status.connected {
  background: rgba(82, 196, 26, 0.25);
  color: #F6FFED;
  border: 2upx solid rgba(183, 235, 143, 0.4);
  box-shadow: 0 0 12upx rgba(82, 196, 26, 0.3);
}

.connection-status.disconnected {
  background: rgba(255, 77, 79, 0.25);
  color: #FFF2F0;
  border: 2upx solid rgba(255, 179, 179, 0.4);
  box-shadow: 0 0 12upx rgba(255, 77, 79, 0.3);
}

.current-time {
  font-size: 24upx;
  color: white;
  font-weight: 600;
  white-space: nowrap;
  letter-spacing: 1upx;
  flex-shrink: 0;
  background: linear-gradient(135deg, #FF8C00, #FF6600);
  padding: 10upx 16upx;
  border-radius: 20upx;
  border: 2upx solid rgba(255, 255, 255, 0.3);
  box-shadow: 0 4upx 12upx rgba(255, 140, 0, 0.4);
  backdrop-filter: blur(8upx);
  text-shadow: 0 1upx 3upx rgba(0, 0, 0, 0.2);
}

.current-time--short {
  display: none;
}

.header-right {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: clamp(8upx, 2vw, 16upx);
  min-width: 0;
  justify-self: end;
  overflow: hidden;
}

.refresh-btn {
  padding: 12upx 24upx; /* 减少内边距 */
  background: rgba(255, 255, 255, 0.2);
  color: white;
  border-radius: 20upx; /* 减少圆角 */
  border: 2upx solid rgba(255, 255, 255, 0.3);
  font-size: 26upx; /* 减少字体大小 */
  font-weight: 600;
  backdrop-filter: blur(8upx);
  transition: all 0.3s ease;
  box-shadow: 0 3upx 8upx rgba(0,0,0,0.12); /* 减少阴影 */
  white-space: nowrap;
  min-width: max(120upx, 44px); /* 减少最小宽度 */
  flex-shrink: 0;
  min-height: max(44upx, 44px); /* 减少最小高度 */
  display: flex;
  align-items: center;
  justify-content: center;
  /* 增强触摸友好性 */
  touch-action: manipulation;
}

.refresh-btn:active {
  background: rgba(255, 255, 255, 0.3);
  transform: scale(0.95);
  box-shadow: 0 2upx 8upx rgba(0,0,0,0.2);
}

.refresh-btn:disabled {
  opacity: 0.5;
  transform: none;
}

/* 🆕 断连告警横条：比打印失败条更醒目（脈动动画），提示 WS 正在重连 */
.disconnect-banner {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12upx;
  padding: 14upx 24upx;
  background: linear-gradient(135deg, #CF1322, #FF4D4F);
  box-shadow: 0 2upx 12upx rgba(207, 19, 34, 0.5);
  animation: disconnect-banner-pulse 1.5s ease-in-out infinite;
}

.disconnect-banner-text {
  color: white;
  font-size: 26upx;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@keyframes disconnect-banner-pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.75;
  }
}

/* 🆕 打印失败提示条 */
.print-fail-banner {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16upx;
  padding: 12upx 24upx;
  background: linear-gradient(135deg, #FF4D4F, #FF7875);
  box-shadow: 0 2upx 8upx rgba(255, 77, 79, 0.3);
}

.print-fail-info {
  display: flex;
  align-items: center;
  gap: 8upx;
  min-width: 0;
  overflow: hidden;
}

.print-fail-text {
  color: white;
  font-size: 24upx;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.print-retry-btn {
  flex-shrink: 0;
  padding: 8upx 20upx;
  background: rgba(255, 255, 255, 0.25);
  border: 2upx solid rgba(255, 255, 255, 0.5);
  border-radius: 16upx;
  color: white;
  font-size: 24upx;
  font-weight: 600;
  touch-action: manipulation;
}

.print-retry-btn:active {
  background: rgba(255, 255, 255, 0.4);
  transform: scale(0.95);
}

/* 🆕 外卖取消告警横条 */
.cancel-banner {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16upx;
  padding: 14upx 24upx;
  background: linear-gradient(135deg, #D46B08, #FA8C16);
  box-shadow: 0 2upx 10upx rgba(212, 107, 8, 0.4);
}

/* 含已出餐/已打票时更醒目：深红 + 脉冲动画 */
.cancel-banner.cooked {
  background: linear-gradient(135deg, #A8071A, #F5222D);
  box-shadow: 0 2upx 14upx rgba(168, 7, 26, 0.55);
  animation: disconnect-banner-pulse 1.2s ease-in-out infinite;
}

.cancel-banner-info {
  display: flex;
  align-items: center;
  gap: 10upx;
  min-width: 0;
  overflow: hidden;
}

.cancel-banner-text {
  color: white;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cancel-banner-title {
  font-size: 26upx;
  font-weight: 700;
}

.cancel-banner-list {
  font-size: 24upx;
  font-weight: 500;
  opacity: 0.95;
}

.cancel-dismiss-btn {
  flex-shrink: 0;
  padding: 8upx 20upx;
  background: rgba(255, 255, 255, 0.25);
  border: 2upx solid rgba(255, 255, 255, 0.5);
  border-radius: 16upx;
  color: white;
  font-size: 24upx;
  font-weight: 600;
  touch-action: manipulation;
}

.cancel-dismiss-btn:active {
  background: rgba(255, 255, 255, 0.4);
  transform: scale(0.95);
}

/* 控制面板 - 纯 Grid 布局 */
.control-panel {
  background: white;
  width: 100%;
  overflow: hidden;
  box-shadow: 0 2upx 8upx rgba(0,0,0,0.1);
  display: grid;
  grid-template-columns: 2fr 1fr 1fr;
  grid-template-areas: "tabs info sort";
  min-height: 120upx;
  gap: 0;
  flex-shrink: 0;
  align-items: stretch;
}

/* 单档口锁定：无 Tab 栏，统计 + 排序均分 */
.control-panel--no-tabs {
  grid-template-columns: 1fr 1fr;
  grid-template-areas: "info sort";
}

/* 中等屏幕：统计行独占一行 */
@media screen and (max-width: 1199px) and (min-width: 768px) {
  .control-panel {
    grid-template-columns: 2fr 1fr;
    grid-template-areas:
      "tabs sort"
      "info info";
    grid-template-rows: auto auto;
  }

  .control-panel--no-tabs {
    grid-template-columns: 1fr;
    grid-template-areas:
      "sort"
      "info";
  }
}

/* 小屏幕：垂直堆叠 */
@media screen and (max-width: 767px) {
  .control-panel {
    grid-template-columns: 1fr;
    grid-template-areas:
      "tabs"
      "info"
      "sort";
    grid-template-rows: auto auto auto;
  }

  .control-panel--no-tabs {
    grid-template-areas:
      "info"
      "sort";
    grid-template-rows: auto auto;
  }
}

.panel-section {
  display: flex;
  flex-direction: column;
  justify-content: center;
  position: relative;
  border-right: 1upx solid #F0F0F0;
  padding: 12upx;
}

.panel-section:last-child {
  border-right: none;
}

/* Grid区域分配 */
.tabs-section {
  grid-area: tabs;
}

.info-section {
  grid-area: info;
}

.sort-section {
  grid-area: sort;
}

/* 分隔线优化 */
.panel-section:not(:last-child)::after {
  content: '';
  position: absolute;
  top: 15%;
  right: -1upx;
  height: 70%;
  width: 1upx;
  background: linear-gradient(to bottom, transparent, #E0E0E0, transparent);
}

/* 小屏幕下 panel 区块改为底部分隔线 */
@media screen and (max-width: 767px) {
  .panel-section {
    border-right: none;
    border-bottom: 1upx solid #F0F0F0;
  }

  .panel-section:last-child {
    border-bottom: none;
  }

  .panel-section:not(:last-child)::after {
    display: none;
  }
}

/* 档口标签区域 - 屏幕宽度自适应 */
.tabs-section {
  overflow: hidden;
  min-width: 0; /* Grid中允许收缩 */
  width: 100%; /* 占满分配的Grid空间 */
  max-width: 100%; /* 防止溢出 */
}

.tabs-scroll {
  width: 100%;
  height: 100%;
  overflow-x: auto; /* 水平滚动 */
  overflow-y: hidden;
  white-space: nowrap;
  /* 平滑滚动 */
  scroll-behavior: smooth;
  /* 隐藏滚动条但保持功能 */
  scrollbar-width: none; /* Firefox */
  -ms-overflow-style: none; /* IE/Edge */
}

.tabs-scroll::-webkit-scrollbar {
  display: none; /* Chrome/Safari/Webkit */
}

.tabs-container {
  display: flex;
  padding: 12upx clamp(8upx, 2vw, 20upx);
  gap: clamp(6upx, 1.5vw, 12upx); /* 更紧凑的响应式间距 */
  height: 100%;
  align-items: stretch;
  flex-wrap: nowrap; /* 保持水平滚动 */
  min-width: max-content; /* 确保内容不被压缩 */
  width: auto;
}

/* 档口标签容器的屏幕适应优化 */
@media screen and (max-width: 767px) {
  .tabs-container {
    padding: 10upx 12upx;
    gap: 6upx;
  }
}

@media screen and (max-width: 479px) {
  .tabs-container {
    padding: 8upx 10upx;
    gap: 4upx;
  }
}

.station-tab {
  min-width: clamp(80upx, 12vw, 120upx); /* 更灵活的响应式宽度 */
  max-width: clamp(120upx, 20vw, 160upx); /* 最大宽度限制，防止过宽 */
  padding: clamp(12upx, 3vw, 18upx) clamp(16upx, 4vw, 20upx);
  background: #F5F5F5;
  border: 3upx solid transparent;
  border-radius: 12upx;
  position: relative;
  transition: all 0.3s ease;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 44px;
  touch-action: manipulation;
  user-select: none;
  -webkit-user-select: none;
  /* 文本溢出处理 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.station-tab.active {
  background: white;
  box-shadow: 0 4upx 16upx rgba(0,0,0,0.15);
  transform: translateY(-2upx);
}

.station-tab.changfen { border-color: #4ECDC4; }
.station-tab.shulong { border-color: #45B7D1; }
.station-tab.xibing { border-color: #FF6B6B; }
.station-tab.mingdang1 { border-color: #96CEB4; }
.station-tab.mingdang2 { border-color: #FECA57; }
.station-tab.jianzha { border-color: #DDA0DD; }
.station-tab.qita { border-color: #A8A8A8; }

/* 选中状态的背景色高亮 - 🌟 鲜艳版本 */
.station-tab.changfen.active { 
  background: linear-gradient(135deg, rgba(78, 205, 196, 0.45), rgba(78, 205, 196, 0.25));
  border-color: #4ECDC4;
  border-width: 4upx;
  box-shadow: 0 6upx 20upx rgba(78, 205, 196, 0.5), 0 0 20upx rgba(78, 205, 196, 0.3);
}
.station-tab.shulong.active { 
  background: linear-gradient(135deg, rgba(69, 183, 209, 0.45), rgba(69, 183, 209, 0.25));
  border-color: #45B7D1;
  border-width: 4upx;
  box-shadow: 0 6upx 20upx rgba(69, 183, 209, 0.5), 0 0 20upx rgba(69, 183, 209, 0.3);
}
.station-tab.xibing.active { 
  background: linear-gradient(135deg, rgba(255, 107, 107, 0.45), rgba(255, 107, 107, 0.25));
  border-color: #FF6B6B;
  border-width: 4upx;
  box-shadow: 0 6upx 20upx rgba(255, 107, 107, 0.5), 0 0 20upx rgba(255, 107, 107, 0.3);
}
.station-tab.mingdang1.active { 
  background: linear-gradient(135deg, rgba(150, 206, 180, 0.45), rgba(150, 206, 180, 0.25));
  border-color: #96CEB4;
  border-width: 4upx;
  box-shadow: 0 6upx 20upx rgba(150, 206, 180, 0.5), 0 0 20upx rgba(150, 206, 180, 0.3);
}
.station-tab.mingdang2.active { 
  background: linear-gradient(135deg, rgba(254, 202, 87, 0.45), rgba(254, 202, 87, 0.25));
  border-color: #FECA57;
  border-width: 4upx;
  box-shadow: 0 6upx 20upx rgba(254, 202, 87, 0.5), 0 0 20upx rgba(254, 202, 87, 0.3);
}
.station-tab.jianzha.active { 
  background: linear-gradient(135deg, rgba(221, 160, 221, 0.45), rgba(221, 160, 221, 0.25));
  border-color: #DDA0DD;
  border-width: 4upx;
  box-shadow: 0 6upx 20upx rgba(221, 160, 221, 0.5), 0 0 20upx rgba(221, 160, 221, 0.3);
}
.station-tab.qita.active { 
  background: linear-gradient(135deg, rgba(168, 168, 168, 0.45), rgba(168, 168, 168, 0.25));
  border-color: #A8A8A8;
  border-width: 4upx;
  box-shadow: 0 6upx 20upx rgba(168, 168, 168, 0.5), 0 0 20upx rgba(168, 168, 168, 0.3);
}

/* 选中状态的文字颜色强调 */
.station-tab.changfen.active .tab-name { color: #4ECDC4; }
.station-tab.shulong.active .tab-name { color: #45B7D1; }
.station-tab.xibing.active .tab-name { color: #FF6B6B; }
.station-tab.mingdang1.active .tab-name { color: #96CEB4; }
.station-tab.mingdang2.active .tab-name { color: #FECA57; }
.station-tab.jianzha.active .tab-name { color: #DDA0DD; }
.station-tab.qita.active .tab-name { color: #A8A8A8; }

.station-tab.changfen.active .tab-count { color: #4ECDC4; }
.station-tab.shulong.active .tab-count { color: #45B7D1; }
.station-tab.xibing.active .tab-count { color: #FF6B6B; }
.station-tab.mingdang1.active .tab-count { color: #96CEB4; }
.station-tab.mingdang2.active .tab-count { color: #FECA57; }
.station-tab.jianzha.active .tab-count { color: #DDA0DD; }
.station-tab.qita.active .tab-count { color: #A8A8A8; }

.tab-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6upx;
  text-align: center;
  width: 100%;
  height: 100%;
  padding-top: 6upx;
}

.tab-name {
  font-size: clamp(20upx, 4vw, 28upx); /* 响应式字体大小 */
  font-weight: bold;
  color: #2C3E50;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: clamp(80upx, 15vw, 120upx); /* 响应式最大宽度 */
  line-height: 1.2;
  text-align: center;
  width: 100%;
  margin-top: 4upx;
}

.tab-info {
  display: flex;
  align-items: baseline;
  gap: 6upx;
  justify-content: center;
  width: 100%;
}

.tab-count {
  font-size: clamp(24upx, 5vw, 32upx); /* 响应式字体大小 */
  font-weight: bold;
  color: #1890FF;
  text-align: center;
  line-height: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tab-unit {
  font-size: clamp(16upx, 3.5vw, 22upx); /* 响应式字体大小 */
  color: #666;
  font-weight: 500;
  text-align: center;
  line-height: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tab-urgent {
  position: absolute;
  top: -6upx;
  right: -6upx;
  background: linear-gradient(135deg, #FF4D4F, #FF7875);
  color: white;
  padding: 6upx 10upx;
  border-radius: 14upx;
  font-size: 20upx;
  line-height: 1;
  min-width: 28upx;
  text-align: center;
  font-weight: bold;
  box-shadow: 0 4upx 12upx rgba(255, 77, 79, 0.6);
  border: 2upx solid white;
  animation: pulse-urgent 2s infinite;
  z-index: 10;
}

/* 档口信息区域 - 固定25%宽度 */
.info-section {
  width: 100%; /* 占满分配的25%空间 */
  min-width: 0; /* Grid中允许收缩 */
  padding: 8upx 12upx; /* 统一内边距 */
  display: flex;
  justify-content: center; /* 内容居中显示 */
  align-items: center;
}

/* 档口名称相关样式已移除 */

/* 迷你统计信息 - 适配组件宽度 */
.stats-mini-section {
  display: flex;
  gap: 8upx;
  justify-content: space-between; /* 均匀分布，充分利用25%空间 */
  width: 100%;
  flex-wrap: nowrap; /* 不换行，强制一行显示 */
}

/* 中等屏幕下统计卡片 */
@media screen and (max-width: 1199px) and (min-width: 768px) {
  .stats-mini-section {
    gap: 10upx;
  }

  .stat-mini-card {
    flex: 1;
    min-width: 0;
  }
}

/* 小屏幕下统计卡片 */
@media screen and (max-width: 767px) {
  .stats-mini-section {
    gap: 8upx;
    justify-content: space-between;
  }

  .stat-mini-card {
    flex: 1;
    min-width: 0;
  }
}

.stat-mini-card {
  text-align: center;
  padding: 8upx 6upx;
  border-radius: 8upx;
  box-shadow: 0 1upx 6upx rgba(0,0,0,0.08);
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.stat-mini-card.urgent {
  background: linear-gradient(135deg, #FF6B6B, #FF8E85);
  color: white;
}

.stat-mini-card.pending {
  background: linear-gradient(135deg, #4ECDC4, #44A08D);
  color: white;
}

.stat-mini-card.completed {
  background: linear-gradient(135deg, #45B7D1, #2196F3);
  color: white;
}

.stat-mini-card.efficiency {
  background: linear-gradient(135deg, #96CEB4, #FFEAA7);
  color: #2C3E50;
}

.stat-mini-value {
  font-size: 34upx; /* 增大字体大小 */
  font-weight: bold;
  display: block;
  margin-bottom: 2upx; /* 减少下边距 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.1; /* 减少行高 */
}

.stat-mini-title {
  font-size: 22upx;
  opacity: 0.9;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
  max-width: 100%;
}

/* 排序选项区域 - Grid优化 */
.sort-section {
  min-width: 0; /* Grid中允许收缩 */
  padding: 8upx 12upx; /* 统一内边距 */
}

.sort-label {
  font-size: 20upx; /* 减少字体大小 */
  color: #666;
  margin-bottom: 6upx; /* 减少下边距 */
  text-align: center;
  white-space: nowrap;
  font-weight: 600;
}

.sort-buttons {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(60px, 1fr)); /* 自动适应按钮数量 */
  gap: 6upx;
  width: 100%;
}

/* 三个按钮时的优化布局 */
@media screen and (min-width: 200px) {
  .sort-buttons {
    grid-template-columns: repeat(3, 1fr); /* 固定3列，对应3个排序按钮 */
  }
}

.sort-btn {
  padding: 6upx 10upx;
  background: #F5F5F5;
  border: 2upx solid transparent;
  border-radius: 8upx;
  font-size: 22upx; /* 增大字体大小 */
  color: #666;
  transition: all 0.3s ease;
  white-space: nowrap;
  font-weight: 600;
  box-shadow: 0 1upx 4upx rgba(0,0,0,0.06);
  min-height: 36upx;
  touch-action: manipulation;
  user-select: none;
  -webkit-user-select: none;
  /* Grid下优化 */
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
}

.sort-btn.active {
  background: #1890FF;
  color: white;
  border-color: #1890FF;
  box-shadow: 0 4upx 12upx rgba(24, 144, 255, 0.3);
}

/* 订单区域 */
.orders-section {
  margin-top: 8upx;
  background: white;
  overflow: hidden;
  box-shadow: 0 2upx 8upx rgba(0,0,0,0.1);
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  width: 100%;
  padding-bottom: calc(env(safe-area-inset-bottom) + 8upx);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24upx 32upx;
  background: #F8F9FA;
  border-bottom: 2upx solid #E8E8E8;
}

.section-title {
  font-size: 36upx;
  font-weight: bold;
  color: #2C3E50;
}

.orders-count {
  font-size: 28upx;
  color: #666;
  font-weight: 500;
}

.orders-scroll {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.loading-container,
.empty-container {
  padding: 150upx;
  text-align: center;
}

.loading-text,
.empty-text {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8upx;
  font-size: 48upx;
  color: #999;
  font-weight: 500;
}

.orders-list {
  padding: clamp(8upx, 1vw, 16upx);
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr));
  gap: clamp(8upx, 1vw, 12upx);
  align-content: start;
  width: 100%;
  box-sizing: border-box;
}

/* 紧凑/超紧凑：缩小列宽，一屏塞更多卡片 */
.orders-list.density-compact {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr));
  gap: clamp(6upx, 0.8vw, 10upx);
}

.orders-list.density-ultra {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 180px), 1fr));
  gap: clamp(4upx, 0.6vw, 8upx);
}

/* 菜品卡片样式见 components/KitchenDishCard */

/* 🆕 批量提交悬浮按钮样式 */
.batch-submit-float {
  position: fixed;
  bottom: calc(24upx + env(safe-area-inset-bottom));
  left: 50%;
  transform: translateX(-50%);
  z-index: 1000;
  width: min(92vw, 640upx);
}

.batch-submit-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16upx;
  padding: 24upx 48upx;
  background: linear-gradient(135deg, #1890FF, #40A9FF);
  color: white;
  border: 3upx solid rgba(255, 255, 255, 0.2);
  border-radius: 24upx;
  font-weight: bold;
  box-shadow: 0 12upx 36upx rgba(24, 144, 255, 0.4);
  transition: all 0.3s ease;
  min-height: 96upx;
  width: 100%;
  touch-action: manipulation;
  user-select: none;
  backdrop-filter: blur(10upx);
  position: relative;
}

.batch-submit-btn:active:not(:disabled) {
  transform: scale(0.95);
  background: linear-gradient(135deg, #096dd9, #1890FF);
  box-shadow: 0 9upx 30upx rgba(24, 144, 255, 0.5);
}

.batch-submit-btn:disabled {
  opacity: 0.7;
  transform: none;
}

.submit-icon {
  font-size: 40upx;
  line-height: 1;
}

.submit-text {
  font-size: 32upx;
  font-weight: bold;
  white-space: nowrap;
  line-height: 1;
}

/* 为悬浮按钮添加脉动效果 */
@keyframes batch-pulse {
  0%, 100% {
    box-shadow: 0 12upx 36upx rgba(24, 144, 255, 0.4);
  }
  50% {
    box-shadow: 0 12upx 48upx rgba(24, 144, 255, 0.6);
  }
}

.batch-submit-btn:not(:disabled) {
  animation: batch-pulse 2s infinite;
}

/* 🆕 模态框样式 */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40upx;
  backdrop-filter: blur(4upx);
}

.modal-container {
  background: white;
  border-radius: 16upx;
  max-width: 80vw;
  max-height: 80vh;
  width: 600upx;
  box-shadow: 0 16upx 48upx rgba(0, 0, 0, 0.3);
  overflow: hidden;
  animation: modalSlideIn 0.3s ease-out;
}

@keyframes modalSlideIn {
  from {
    opacity: 0;
    transform: translateY(-20upx) scale(0.95);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24upx 32upx;
  background: linear-gradient(135deg, #1890FF, #40A9FF);
  color: white;
}

.modal-title {
  font-size: 32upx;
  font-weight: bold;
}

.modal-close {
  width: 48upx;
  height: 48upx;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
  border: none;
  color: white;
  font-size: 24upx;
  font-weight: bold;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
}

.modal-close:active {
  background: rgba(255, 255, 255, 0.3);
  transform: scale(0.95);
}

.modal-content {
  padding: 32upx;
  max-height: 60vh;
  overflow-y: auto;
}

.detail-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16upx 0;
  border-bottom: 1upx solid #F0F0F0;
}

.detail-section:last-child {
  border-bottom: none;
}

.detail-label {
  font-size: 28upx;
  color: #666;
  font-weight: 500;
}

.detail-value {
  font-size: 28upx;
  color: #333;
  font-weight: 600;
}

.detail-value.urgent {
  color: #FF4D4F;
}

.detail-value.high {
  color: #FA8C16;
}

.detail-value.normal {
  color: #52C41A;
}

.orders-detail-section {
  margin-top: 24upx;
  border-top: 2upx solid #F0F0F0;
  padding-top: 24upx;
}

.section-title {
  font-size: 30upx;
  color: #333;
  font-weight: bold;
  margin-bottom: 16upx;
  display: block;
}

.orders-list-modal {
  display: flex;
  flex-direction: column;
  gap: 12upx;
}

.order-item {
  background: #F8F9FA;
  border-radius: 12upx;
  padding: 16upx;
  border: 1upx solid #E8E8E8;
}

.order-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8upx;
}

.order-table {
  font-size: 26upx;
  color: #1890FF;
  font-weight: bold;
}

.order-quantity {
  font-size: 24upx;
  color: #666;
  font-weight: 500;
}

.order-time {
  font-size: 24upx;
  color: #999;
  font-weight: 400;
}

/* 底部工具栏 - 平板优化 - 已注释掉 */
/* .toolbar {
  position: fixed;
  bottom: 60upx;
  left: 32upx;
  right: 32upx;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  border-radius: 20upx;
  padding: 32upx;
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-shadow: 0 8upx 32upx rgba(0,0,0,0.15);
  min-height: 80upx;
}

.station-status {
  font-size: 32upx;
  padding: 16upx 24upx;
  border-radius: 24upx;
  font-weight: 600;
  box-shadow: 0 2upx 8upx rgba(0,0,0,0.1);
}

.station-status.urgent {
  background: #FFE8E8;
  color: #FF4D4F;
  box-shadow: 0 4upx 12upx rgba(255, 77, 79, 0.2);
}

.station-status.busy {
  background: #FFF7E6;
  color: #FA8C16;
  box-shadow: 0 4upx 12upx rgba(250, 140, 22, 0.2);
}

.station-status.normal {
  background: #E8F5E8;
  color: #52C41A;
  box-shadow: 0 4upx 12upx rgba(82, 196, 26, 0.2);
}

.station-status.idle {
  background: #F0F0F0;
  color: #999;
}

.auto-refresh-info {
  font-size: 28upx;
  color: #666;
  font-weight: 500;
} */

/* 更新信息 - 平板优化 - 已注释掉 */
/* .update-info {
  position: fixed;
  bottom: 8upx;
  left: 32upx;
  right: 32upx;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 26upx;
  color: #999;
  padding: 16upx 24upx;
  background: rgba(255, 255, 255, 0.9);
  border-radius: 16upx;
  backdrop-filter: blur(8px);
  font-weight: 500;
} */

/* 动画效果 */
@keyframes pulse-urgent {
  0%, 100% {
    transform: scale(1);
    box-shadow: 0 4upx 12upx rgba(255, 77, 79, 0.6);
  }
  50% {
    transform: scale(1.05);
    box-shadow: 0 6upx 16upx rgba(255, 77, 79, 0.7);
  }
}



/* 菜品排序过渡动画 - 适配Grid布局 */
.dishes-container {
  position: relative;
  width: 100%;
  /* 继承父级的grid布局 */
  display: contents; /* 让子元素直接参与父级的grid布局 */
}

.dish-sort-move {
  transition: all 0.6s cubic-bezier(0.25, 0.8, 0.25, 1);
}

.dish-sort-enter-active {
  transition: all 0.4s cubic-bezier(0.25, 0.8, 0.25, 1);
}

.dish-sort-leave-active {
  transition: all 0.4s cubic-bezier(0.25, 0.8, 0.25, 1);
  position: absolute;
  /* Grid布局下自动适应宽度 */
  width: auto;
  z-index: 0;
}

.dish-sort-enter-from {
  opacity: 0;
  transform: translateY(-30upx) scale(0.95);
}

.dish-sort-leave-to {
  opacity: 0;
  transform: translateY(30upx) scale(0.95);
}

/* ========== 统一响应式断点（px） ========== */

/* 中等宽度：压缩 header，避免三栏重叠 */
@media screen and (min-width: 768px) and (max-width: 1199px) {
  .header-content {
    padding: 14upx 16upx;
    column-gap: 12upx;
  }

  .back-text {
    display: none;
  }

  .current-time--full {
    display: none;
  }

  .current-time--short {
    display: block;
    font-size: 20upx;
    padding: 8upx 12upx;
  }

  .connection-status {
    font-size: 20upx;
    padding: 8upx 12upx;
  }

  .refresh-btn {
    padding: 8upx 16upx;
    font-size: 22upx;
    min-width: auto;
  }

  .main-title {
    font-size: 30upx;
  }

  .orders-list {
    padding: 12upx 16upx;
  }
}

/* 大屏厨房平板：三栏 Grid，标题始终居中 */
@media screen and (min-width: 1200px) {
  .header-content {
    padding: 16upx 32upx;
    column-gap: 24upx;
  }

  .current-time--full {
    display: block;
  }

  .current-time--short {
    display: none;
  }

  .main-title {
    font-size: 38upx;
  }

  .orders-list {
    padding: 12upx 24upx;
  }

  .station-tab {
    min-width: 140upx;
  }
}

/* 小屏手机竖屏 */
@media screen and (max-width: 767px) {
  .header-content {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 10upx;
    padding: 12upx 16upx;
  }

  .header-left,
  .header-right {
    width: 100%;
    justify-self: stretch;
    justify-content: space-between;
  }

  .header-center {
    width: 100%;
    justify-self: stretch;
    padding: 0;
  }

  .current-time--full {
    display: block;
    font-size: 22upx;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .current-time--short {
    display: none;
  }

  .main-title {
    font-size: 32upx;
    text-align: center;
  }

  .back-btn,
  .refresh-btn {
    padding: 10upx 16upx;
    font-size: 24upx;
    min-width: auto;
  }

  .connection-status {
    font-size: 22upx;
    padding: 10upx 14upx;
    min-width: auto;
  }

  .tabs-container {
    padding: 0;
    gap: 8upx;
  }

  .station-tab {
    min-width: 90upx;
    padding: 10upx 12upx;
  }

  .tab-name {
    font-size: 22upx;
    max-width: 90upx;
  }

  .stat-mini-value {
    font-size: 28upx;
  }

  .stat-mini-title {
    font-size: 20upx;
  }

  .orders-list {
    grid-template-columns: 1fr;
    padding: 8upx;
    gap: 8upx;
  }

  .dish-card {
    min-height: 260upx;
    padding: 12upx;
  }

  .dish-name {
    font-size: 28upx;
  }

  .quantity-text {
    font-size: 36upx;
  }

  .time-value {
    font-size: 28upx;
  }

  .batch-submit-btn {
    padding: 20upx 32upx;
    min-height: 80upx;
    gap: 12upx;
  }

  .submit-icon {
    font-size: 32upx;
  }

  .submit-text {
    font-size: 28upx;
  }
}

/* 超小屏手机 */
@media screen and (max-width: 479px) {
  .header-content {
    padding: 10upx 12upx;
  }

  .main-title {
    font-size: 28upx;
  }

  .current-time {
    font-size: 18upx;
    padding: 6upx 10upx;
  }

  .back-btn,
  .refresh-btn {
    padding: 8upx 12upx;
    font-size: 20upx;
  }

  .connection-status {
    font-size: 18upx;
    padding: 6upx 10upx;
  }

  .station-tab {
    min-width: 72upx;
    padding: 8upx 10upx;
  }

  .tab-name {
    font-size: 20upx;
    max-width: 72upx;
  }

  .tab-count {
    font-size: 24upx;
  }

  .stat-mini-value {
    font-size: 24upx;
  }

  .stat-mini-title {
    font-size: 18upx;
  }

  .sort-btn {
    font-size: 20upx;
    padding: 6upx 8upx;
  }

  .dish-card {
    min-height: 240upx;
    padding: 10upx;
  }

  .dish-name {
    font-size: 26upx;
  }

  .quantity-text {
    font-size: 32upx;
  }

  .batch-submit-float {
    width: min(96vw, 560upx);
  }

  .batch-submit-btn {
    padding: 16upx 24upx;
    min-height: 72upx;
  }

  .submit-text {
    font-size: 24upx;
  }
}

/* 减少动画效果（用户偏好） */
@media (prefers-reduced-motion: reduce) {
  .dish-card,
  .station-tab,
  .sort-btn,
  .complete-btn {
    transition: none;
  }
  
  .dish-card:hover {
    transform: none;
  }
}

/* 高对比度模式支持已简化，保持原有边框样式 */
</style>