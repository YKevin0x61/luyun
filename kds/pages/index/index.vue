<template>
  <view class="dashboard-page">
    <view class="top-bar">
      <text class="brand">KDS 首页</text>
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

    <view class="cockpit">
      <view class="overview-pane" :class="{ 'overview-pane--alert': overview.alertPrimary }">
        <view class="status-pill" :class="systemStatusClass">
          <view class="indicator-dot" :class="systemStatusClass"></view>
          <text class="status-text">{{ systemStatusText }}</text>
        </view>

        <view class="overview-metric" :class="{ 'overview-metric--dim': !overview.numbersTrusted }">
          <text class="overview-metric-label">待制作</text>
          <text class="overview-metric-value">{{ kitchenStats.total }}</text>
        </view>

        <view class="overview-pair">
          <view class="overview-box" :class="{ 'overview-metric--dim': !overview.numbersTrusted }">
            <text class="overview-metric-label">紧急</text>
            <text class="overview-metric-value urgent">{{ kitchenStats.urgent }}</text>
          </view>
          <view class="overview-box refresh-box" @click="refreshData">
            <SvgIcon
              class="refresh-icon"
              :class="{ rotating: loading }"
              name="refresh-cw"
              :size="20"
              color="#1890ff"
            />
            <text class="refresh-label">刷新</text>
          </view>
        </view>

        <text v-if="overview.alertPrimary" class="alert-hint">
          连接异常 · 告警优先，待做/紧急数字仅供参考
        </text>
        <text v-else class="scope-hint">本屏职责 · {{ watchedScopeLabel }}</text>

        <view class="cta-btn" @click="navigateToKitchen()">
          <text class="cta-title">进入厨房</text>
          <text class="cta-desc">{{ kitchenCtaDesc }}</text>
        </view>
      </view>

      <view class="stations-pane">
        <view class="section-header">
          <text class="section-title">职责档口</text>
          <text class="section-desc">点卡片进入对应档口</text>
        </view>

        <view v-if="stationStatus.length" class="station-mosaic">
          <view
            v-for="station in stationStatus"
            :key="station.id"
            class="station-card"
            :class="{ active: station.active }"
            @click="navigateToKitchen(station.id)"
          >
            <view class="station-card-head">
              <view
                class="station-dot"
                :style="{ background: station.active ? (station.color || '#52c41a') : '#d9d9d9' }"
              ></view>
              <text class="station-name">{{ station.name }}</text>
            </view>
            <view class="station-stats">
              <view class="station-stat" :class="{ hot: station.pendingCount > 0 }">
                <text class="station-stat-value">{{ station.pendingCount }}</text>
                <text class="station-stat-label">待制作</text>
              </view>
              <view class="station-stat" :class="{ urgent: station.urgentCount > 0 }">
                <text class="station-stat-value">{{ station.urgentCount }}</text>
                <text class="station-stat-label">紧急</text>
              </view>
            </view>
          </view>
        </view>

        <view v-else class="empty-stations">
          <text class="empty-text">暂无档口数据</text>
        </view>
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
import { hubOverviewPresentation } from '../../utils/hubOverviewPresentation.js'
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

    const overview = computed(() =>
      hubOverviewPresentation(realtimeStore.connectionStatus)
    )

    const systemStatusClass = computed(() => {
      if (realtimeStore.connectionStatus === 'connected') return 'status-online'
      if (realtimeStore.connectionStatus === 'reconnecting') return 'status-error'
      return 'status-offline'
    })

    const systemStatusText = computed(() => {
      const statusMap = {
        connected: '连接健康',
        reconnecting: '重连中...',
        disconnected: '连接中断'
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
      if (ids.length === 1) return '单档口锁定'
      return `${ids.length} 个档口`
    })

    /** CTA subline: no station query = current watched set (not always「全部」). */
    const kitchenCtaDesc = computed(() => {
      const ids = watchedStationIds.value
      if (!ids.length) return '本屏职责档口集 · 全部档口'
      if (ids.length === 1) return '本屏职责档口集 · 单档口锁定'
      return `本屏职责档口集 · ${ids.length} 个档口`
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
      overview,
      systemStatusClass,
      systemStatusText,
      kitchenStats,
      unmappedCount,
      unmappedBadgeText,
      stationStatus,
      watchedScopeLabel,
      kitchenCtaDesc,
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
  background: #eef1f4;
  padding: 24upx 24upx 48upx;
  padding-top: calc(24upx + env(safe-area-inset-top));
  box-sizing: border-box;
}

.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20upx;
}

.brand {
  font-size: 30upx;
  font-weight: 700;
  color: #1a2332;
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
  color: #5b6573;
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

.cockpit {
  display: flex;
  flex-direction: column;
  gap: 20upx;
  background: #ffffff;
  border-radius: 18upx;
  border: 1upx solid #d5dbe3;
  overflow: hidden;
  box-shadow: 0 2upx 12upx rgba(26, 35, 50, 0.06);
  min-height: calc(100vh - 140upx);
}

.overview-pane {
  display: flex;
  flex-direction: column;
  gap: 20upx;
  padding: 28upx 24upx;
  background: #f7f9fc;
  border-bottom: 1upx solid #d5dbe3;
}

.overview-pane--alert {
  background: #fff4f4;
}

.status-pill {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 10upx;
  padding: 8upx 16upx;
  border-radius: 999upx;
  background: #e6f6ec;
}

.status-pill.status-error,
.status-pill.status-offline {
  background: #fdecea;
}

.indicator-dot {
  width: 14upx;
  height: 14upx;
  border-radius: 50%;
}

.indicator-dot.status-online {
  background: #1f8a4c;
  box-shadow: 0 0 8upx rgba(31, 138, 76, 0.45);
}

.indicator-dot.status-error {
  background: #c62828;
  animation: blink 1s infinite;
}

.indicator-dot.status-offline {
  background: #c62828;
}

.status-text {
  font-size: 22upx;
  font-weight: 600;
  color: #1a2332;
}

.overview-metric-label {
  display: block;
  font-size: 22upx;
  color: #5b6573;
  margin-bottom: 4upx;
}

.overview-metric-value {
  display: block;
  font-size: 72upx;
  font-weight: 800;
  line-height: 1;
  color: #1a2332;
}

.overview-metric-value.urgent {
  color: #c62828;
  font-size: 44upx;
}

.overview-metric--dim {
  opacity: 0.45;
}

.overview-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12upx;
}

.overview-box {
  background: #ffffff;
  border: 1upx solid #d5dbe3;
  border-radius: 14upx;
  padding: 16upx;
}

.refresh-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8upx;
}

.refresh-label {
  font-size: 22upx;
  color: #1890ff;
  font-weight: 600;
}

.refresh-icon {
  transition: transform 0.6s ease;
}

.refresh-icon.rotating {
  animation: spin 1s linear infinite;
}

.alert-hint {
  font-size: 24upx;
  font-weight: 600;
  color: #c62828;
  line-height: 1.4;
}

.scope-hint {
  font-size: 24upx;
  color: #5b6573;
}

.cta-btn {
  margin-top: auto;
  background: #0b6bcb;
  border-radius: 14upx;
  padding: 28upx 24upx;
}

.cta-btn:active {
  opacity: 0.92;
}

.cta-title {
  display: block;
  font-size: 34upx;
  font-weight: 700;
  color: #ffffff;
}

.cta-desc {
  display: block;
  margin-top: 6upx;
  font-size: 22upx;
  color: rgba(255, 255, 255, 0.88);
}

.stations-pane {
  flex: 1;
  padding: 24upx;
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
  color: #1a2332;
}

.section-desc {
  font-size: 22upx;
  color: #5b6573;
}

.station-mosaic {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14upx;
}

.station-card {
  background: #ffffff;
  border: 1upx solid #d5dbe3;
  border-radius: 14upx;
  padding: 20upx;
}

.station-card:active {
  border-color: #0b6bcb;
  background: #f7fbff;
}

.station-card-head {
  display: flex;
  align-items: center;
  gap: 12upx;
  margin-bottom: 16upx;
}

.station-dot {
  width: 16upx;
  height: 16upx;
  border-radius: 50%;
  flex-shrink: 0;
}

.station-name {
  font-size: 28upx;
  font-weight: 600;
  color: #1a2332;
}

.station-stats {
  display: flex;
  gap: 24upx;
}

.station-stat-value {
  display: block;
  font-size: 36upx;
  font-weight: 700;
  color: #1a2332;
  line-height: 1.1;
}

.station-stat-label {
  display: block;
  font-size: 20upx;
  color: #5b6573;
  margin-top: 4upx;
}

.station-stat.hot .station-stat-value {
  color: #c47a00;
}

.station-stat.urgent .station-stat-value {
  color: #c62828;
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

/* Landscape tablet (≥1200px): split cockpit */
@media screen and (min-width: 1200px) {
  .dashboard-page {
    padding: 20px 24px 32px;
  }

  .brand {
    font-size: 18px;
  }

  .nav-link {
    font-size: 14px;
  }

  .cockpit {
    flex-direction: row;
    min-height: calc(100vh - 88px);
    border-radius: 14px;
  }

  .overview-pane {
    width: 340px;
    flex-shrink: 0;
    border-bottom: none;
    border-right: 1px solid #d5dbe3;
    padding: 22px 20px;
  }

  .overview-metric-value {
    font-size: 48px;
  }

  .overview-metric-value.urgent {
    font-size: 28px;
  }

  .stations-pane {
    padding: 20px;
  }

  .section-title {
    font-size: 18px;
  }

  .station-mosaic {
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
  }

  .station-name {
    font-size: 16px;
  }

  .station-stat-value {
    font-size: 22px;
  }

  .cta-title {
    font-size: 18px;
  }
}

@media screen and (min-width: 1400px) {
  .station-mosaic {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
