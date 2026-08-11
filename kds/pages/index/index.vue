<template>
  <view class="dashboard-page">
    <view class="top-bar">
      <view class="status-indicator">
        <view class="indicator-dot" :class="systemStatusClass"></view>
        <text class="status-text">{{ systemStatusText }}</text>
      </view>
      <view class="nav-links">
        <text class="nav-link" @click="navigateToSettings">设置</text>
        <text class="nav-link" @click="navigateToOrders">订单</text>
        <view class="nav-link-wrap" @click="navigateToManagement">
          <text class="nav-link">管理</text>
          <view v-if="unmappedCount > 0" class="nav-badge">
            <text class="nav-badge-text">{{ unmappedBadgeText }}</text>
          </view>
        </view>
      </view>
    </view>

    <view class="hero-section">
      <view class="cta-card" @click="navigateToKitchen()">
        <text class="cta-title">进入厨房</text>
        <text class="cta-desc">本屏职责档口控菜</text>
        <view class="cta-arrow">
          <SvgIcon name="chef-hat" :size="28" color="#fff" />
        </view>
      </view>

      <view class="metrics-row">
        <view class="metric-block">
          <text class="metric-value">{{ kitchenStats.total }}</text>
          <text class="metric-label">待制作</text>
        </view>
        <view class="metric-block" :class="{ urgent: kitchenStats.urgent > 0 }">
          <text class="metric-value">{{ kitchenStats.urgent }}</text>
          <text class="metric-label">紧急</text>
        </view>
        <view class="metric-block refresh-block" @click.stop="refreshData">
          <SvgIcon
            class="refresh-icon"
            :class="{ rotating: loading }"
            name="refresh-cw"
            :size="18"
            color="#1890ff"
          />
          <text class="metric-label">刷新</text>
        </view>
      </view>
    </view>

    <view class="stations-section">
      <view class="section-header">
        <text class="section-title">职责档口</text>
        <text class="section-desc">{{ watchedScopeLabel }}</text>
      </view>

      <view v-if="stationStatus.length" class="station-list">
        <view
          v-for="station in stationStatus"
          :key="station.id"
          class="station-row"
          :class="{ active: station.active }"
          @click="navigateToKitchen(station.id)"
        >
          <view
            class="station-dot"
            :style="{ background: station.active ? (station.color || '#52c41a') : '#d9d9d9' }"
          ></view>
          <text class="station-name">{{ station.name }}</text>
          <text class="station-count" :class="{ hot: station.pendingCount > 0 }">
            {{ station.pendingCount }}
          </text>
          <text class="station-count-label">待做</text>
        </view>
      </view>

      <view v-else class="empty-stations">
        <text class="empty-text">暂无档口数据</text>
      </view>
    </view>
  </view>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { useOrdersStore } from '../../stores/orders.js'
import { useRealtimeStore } from '../../stores/realtime.js'
import { useStationsStore } from '../../stores/stations.js'
import { TimeCalculator } from '../../utils/timeCalculator.js'
import { DISH_STATUS } from '../../utils/constants.js'
import { ScreenSettingsManager } from '../../utils/storage.js'
import {
  buildWatchedStationStatuses,
  countPendingAndUrgent,
  countUnmappedDishNames,
  filterMergedDishesByWatched
} from '../../utils/dashboardStats.js'
import { useNudgePull } from '../../composables/useNudgePull.js'
import SvgIcon from '../../components/SvgIcon/SvgIcon.vue'

const ORDERS_SUBSCRIPTION_ID = 'kds-dashboard-orders'
const UNMAPPED_BADGE_MAX = 99

function navigateSafely(url) {
  try {
    uni.navigateTo({
      url,
      fail: (err) => {
        console.warn('页面跳转失败:', err)
        uni.showToast({ title: '页面跳转失败', icon: 'error', duration: 2000 })
      }
    })
  } catch (error) {
    console.warn('页面跳转异常:', error)
    uni.showToast({ title: '页面跳转异常', icon: 'error', duration: 2000 })
  }
}

export default {
  name: 'DashboardPage',
  components: { SvgIcon },

  setup() {
    const ordersStore = useOrdersStore()
    const realtimeStore = useRealtimeStore()
    const stationsStore = useStationsStore()

    const loading = ref(false)
    const watchedStationIds = ref(ScreenSettingsManager.getWatchedStations())

    const syncWatchedStations = () => {
      watchedStationIds.value = ScreenSettingsManager.getWatchedStations()
    }

    const systemStatusClass = computed(() => {
      if (realtimeStore.connectionStatus === 'connected') return 'status-online'
      if (realtimeStore.connectionStatus === 'reconnecting') return 'status-error'
      return 'status-offline'
    })

    const systemStatusText = computed(() => {
      const statusMap = {
        connected: '系统正常',
        reconnecting: '重连中...',
        disconnected: '离线状态'
      }
      return statusMap[realtimeStore.connectionStatus] || '未知状态'
    })

    const watchedDishes = computed(() =>
      filterMergedDishesByWatched(ordersStore.mergedDishes, watchedStationIds.value)
    )

    const kitchenStats = computed(() =>
      countPendingAndUrgent(watchedDishes.value, DISH_STATUS.PENDING)
    )

    const unmappedCount = computed(() => {
      const qitaId = stationsStore.getStationById('qita')?.id || 'qita'
      return countUnmappedDishNames(ordersStore.mergedDishes, qitaId)
    })

    const unmappedBadgeText = computed(() =>
      unmappedCount.value > UNMAPPED_BADGE_MAX ? `${UNMAPPED_BADGE_MAX}+` : String(unmappedCount.value)
    )

    const stationStatus = computed(() =>
      buildWatchedStationStatuses(
        stationsStore.stationList,
        ordersStore.mergedDishes,
        watchedStationIds.value,
        DISH_STATUS.PENDING
      )
    )

    const watchedScopeLabel = computed(() => {
      const ids = watchedStationIds.value
      if (!ids.length) return '全部档口'
      if (ids.length === 1) return '单档口'
      return `${ids.length} 个档口`
    })

    const navigateToKitchen = (stationId) => {
      if (stationId) {
        navigateSafely(`/pages/kitchen/kitchen?station=${encodeURIComponent(stationId)}`)
        return
      }
      navigateSafely('/pages/kitchen/kitchen')
    }

    const navigateToManagement = () => {
      // #ifdef H5
      window.location.href = '/admin/'
      // #endif
      // #ifndef H5
      uni.showToast({ title: '请使用浏览器访问 /admin/', icon: 'none' })
      // #endif
    }

    const navigateToSettings = () => navigateSafely('/pages/settings/settings')
    const navigateToOrders = () => navigateSafely('/pages/orders/orders')

    const refreshOrders = async () => {
      await ordersStore.fetchTodayOrders()
    }

    const refreshData = async () => {
      if (loading.value) return
      loading.value = true
      try {
        syncWatchedStations()
        await Promise.all([
          stationsStore.initializeStations(),
          refreshOrders()
        ])
      } catch (error) {
        console.error('刷新数据失败:', error)
        uni.showToast({ title: '刷新失败', icon: 'error', duration: 2000 })
      } finally {
        loading.value = false
      }
    }

    const todayDateStr = TimeCalculator.formatTime(new Date(), 'YYYY-MM-DD')
    useNudgePull({
      id: ORDERS_SUBSCRIPTION_ID,
      topics: ['orders'],
      filters: { date: todayDateStr },
      pull: refreshOrders,
      fallback: 'reconcile',
    })

    onShow(() => {
      syncWatchedStations()
    })

    onMounted(async () => {
      await refreshData()
    })

    return {
      loading,
      systemStatusClass,
      systemStatusText,
      kitchenStats,
      unmappedCount,
      unmappedBadgeText,
      stationStatus,
      watchedScopeLabel,
      navigateToKitchen,
      navigateToManagement,
      navigateToSettings,
      navigateToOrders,
      refreshData
    }
  }
}
</script>

<style scoped>
.dashboard-page {
  min-height: 100vh;
  background: #f5f5f5;
  padding: 24upx 24upx 48upx;
  padding-top: calc(24upx + env(safe-area-inset-top));
  box-sizing: border-box;
}

.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 28upx;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 10upx;
}

.indicator-dot {
  width: 16upx;
  height: 16upx;
  border-radius: 50%;
}

.indicator-dot.status-online {
  background: #52c41a;
  box-shadow: 0 0 8upx rgba(82, 196, 26, 0.55);
}

.indicator-dot.status-error {
  background: #ff4d4f;
  animation: blink 1s infinite;
}

.indicator-dot.status-offline {
  background: #d9d9d9;
}

.status-text {
  font-size: 24upx;
  color: #666666;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 28upx;
}

.nav-link-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.nav-link {
  font-size: 26upx;
  color: #1890ff;
  font-weight: 500;
}

.nav-badge {
  position: absolute;
  top: -14upx;
  right: -22upx;
  min-width: 28upx;
  height: 28upx;
  padding: 0 6upx;
  border-radius: 14upx;
  background: #ff4d4f;
  display: flex;
  align-items: center;
  justify-content: center;
}

.nav-badge-text {
  color: #fff;
  font-size: 18upx;
  font-weight: 700;
  line-height: 1;
}

.hero-section {
  margin-bottom: 32upx;
}

.cta-card {
  background: linear-gradient(135deg, #1890ff 0%, #096dd9 100%);
  border-radius: 20upx;
  padding: 48upx 36upx;
  position: relative;
  overflow: hidden;
  box-shadow: 0 8upx 24upx rgba(24, 144, 255, 0.28);
}

.cta-card:active {
  transform: scale(0.985);
}

.cta-title {
  display: block;
  font-size: 44upx;
  font-weight: 700;
  color: #ffffff;
  margin-bottom: 12upx;
}

.cta-desc {
  display: block;
  font-size: 24upx;
  color: rgba(255, 255, 255, 0.88);
}

.cta-arrow {
  position: absolute;
  right: 36upx;
  top: 50%;
  transform: translateY(-50%);
  width: 80upx;
  height: 80upx;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.18);
  display: flex;
  align-items: center;
  justify-content: center;
}

.metrics-row {
  margin-top: 20upx;
  display: flex;
  gap: 16upx;
}

.metric-block {
  flex: 1;
  background: #ffffff;
  border-radius: 12upx;
  padding: 20upx 12upx;
  display: flex;
  flex-direction: column;
  align-items: center;
  box-shadow: 0 2upx 8upx rgba(0, 0, 0, 0.06);
}

.metric-block.urgent .metric-value {
  color: #ff4d4f;
}

.metric-value {
  font-size: 36upx;
  font-weight: 700;
  color: #1a1a1a;
  margin-bottom: 6upx;
}

.metric-label {
  font-size: 22upx;
  color: #999999;
}

.refresh-block {
  flex: 0.7;
  justify-content: center;
  gap: 8upx;
}

.refresh-icon {
  transition: transform 0.6s ease;
}

.refresh-icon.rotating {
  animation: spin 1s linear infinite;
}

.stations-section {
  background: #ffffff;
  border-radius: 16upx;
  padding: 24upx;
  box-shadow: 0 2upx 8upx rgba(0, 0, 0, 0.06);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 16upx;
}

.section-title {
  font-size: 30upx;
  font-weight: 600;
  color: #1a1a1a;
}

.section-desc {
  font-size: 22upx;
  color: #999999;
}

.station-list {
  display: flex;
  flex-direction: column;
}

.station-row {
  display: flex;
  align-items: center;
  padding: 22upx 8upx;
  border-bottom: 1upx solid #f0f0f0;
}

.station-row:last-child {
  border-bottom: none;
}

.station-row:active {
  background: #f7faff;
}

.station-dot {
  width: 16upx;
  height: 16upx;
  border-radius: 50%;
  margin-right: 16upx;
}

.station-name {
  flex: 1;
  font-size: 28upx;
  color: #1a1a1a;
  font-weight: 500;
}

.station-count {
  font-size: 32upx;
  font-weight: 700;
  color: #1a1a1a;
  min-width: 48upx;
  text-align: right;
  margin-right: 8upx;
}

.station-count.hot {
  color: #1890ff;
}

.station-count-label {
  font-size: 22upx;
  color: #999999;
  width: 56upx;
}

.empty-stations {
  padding: 48upx 0;
  text-align: center;
}

.empty-text {
  font-size: 24upx;
  color: #999999;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0.3; }
}

@media screen and (max-width: 599upx) {
  .metrics-row {
    gap: 12upx;
  }

  .metric-value {
    font-size: 32upx;
  }

  .cta-title {
    font-size: 40upx;
  }

  .cta-arrow {
    width: 68upx;
    height: 68upx;
    right: 28upx;
  }
}

@media screen and (min-width: 750upx) {
  .dashboard-page {
    max-width: 960upx;
    margin: 0 auto;
    padding: 32upx 40upx 56upx;
  }

  .station-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
  }

  .station-row {
    border-bottom: 1upx solid #f0f0f0;
    border-right: 1upx solid #f0f0f0;
    padding: 24upx 16upx;
  }

  .station-row:nth-child(2n) {
    border-right: none;
  }
}

@media screen and (min-width: 1024upx) {
  .cta-card {
    padding: 56upx 48upx;
  }

  .cta-title {
    font-size: 52upx;
  }

  .station-list {
    grid-template-columns: repeat(3, 1fr);
  }

  .station-row:nth-child(2n) {
    border-right: 1upx solid #f0f0f0;
  }

  .station-row:nth-child(3n) {
    border-right: none;
  }
}

@media screen and (max-height: 500upx) and (orientation: landscape) {
  .dashboard-page {
    padding: 16upx 20upx 28upx;
  }

  .top-bar {
    margin-bottom: 16upx;
  }

  .cta-card {
    padding: 28upx 28upx;
  }

  .cta-title {
    font-size: 34upx;
    margin-bottom: 4upx;
  }

  .cta-desc {
    font-size: 20upx;
  }

  .metrics-row {
    margin-top: 12upx;
  }

  .station-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
}
</style>
