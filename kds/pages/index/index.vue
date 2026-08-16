<template>
  <view class="dashboard-page">
    <view class="cockpit">
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

      <view class="cockpit-body">
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
                color="#0b6bcb"
              />
              <text class="refresh-label">刷新</text>
            </view>
          </view>

          <text v-if="overview.alertPrimary" class="alert-hint">
            连接异常 · 告警优先，待做/紧急数字仅供参考
          </text>
          <text v-else class="scope-hint">全店各档 · {{ watchedScopeLabel }}</text>

          <view class="cta-btn" @click="enterKitchenOrSettings">
            <text class="cta-title">进入厨房</text>
            <text class="cta-desc">{{ kitchenCtaDesc }}</text>
          </view>
        </view>

        <view class="stations-pane">
          <view class="section-header">
            <text class="section-title">全部档口</text>
            <text class="section-desc">数字总览，进厨房只进本屏档口</text>
          </view>

          <view v-if="stationStatus.length" class="station-mosaic">
            <view
              v-for="station in stationStatus"
              :key="station.id"
              class="station-card"
              :class="{ active: station.active, 'station-card--locked': station.locked }"
            >
              <view class="station-card-head">
                <view
                  class="station-dot"
                  :style="{ background: station.active ? (station.color || '#52c41a') : '#d9d9d9' }"
                ></view>
                <text class="station-name">{{ station.name }}</text>
                <text v-if="station.locked" class="station-lock-mark">本屏</text>
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
              <view class="station-stats station-stats--secondary">
                <view class="station-stat">
                  <text class="station-stat-value">{{ station.completedToday }}</text>
                  <text class="station-stat-label">已制作</text>
                </view>
                <view class="station-stat">
                  <text class="station-stat-value">{{ station.avgCookingTime }}</text>
                  <text class="station-stat-label">平均制作</text>
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
  countUnmappedDishNames
} from '../../utils/dashboardStats.js'
import { hubOverviewPresentation } from '../../utils/hubOverviewPresentation.js'
import { hubShouldPull } from '../../utils/serveConfirm.js'
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

    const lockedStationId = computed(() =>
      watchedStationIds.value.length === 1 ? watchedStationIds.value[0] : null
    )

    const kitchenStats = computed(() =>
      countPendingAndUrgent(ordersStore.mergedDishes, DISH_STATUS.PENDING)
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
        [],
        DISH_STATUS.PENDING
      ).map((station) => ({
        ...station,
        locked: station.id === lockedStationId.value
      }))
    )

    const lockedStationName = computed(() => {
      const id = lockedStationId.value
      if (!id) return ''
      return stationsStore.getStationById(id)?.name || id
    })

    const watchedScopeLabel = computed(() => {
      if (!lockedStationId.value) return '未设档口'
      return `本屏锁定 ${lockedStationName.value}`
    })

    const kitchenCtaDesc = computed(() => {
      if (lockedStationId.value) return `进入本屏档口 · ${lockedStationName.value}`
      return '先在设置里选定本屏档口'
    })

    const enterKitchenOrSettings = () => {
      if (lockedStationId.value) {
        navigateSafely('/pages/kitchen/kitchen')
        return
      }
      navigateSafely('/pages/settings/settings')
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

    // 出餐 nudge must not download today's full 订单行; mount / 刷新 / 60s reconcile still pull.
    const todayDateStr = TimeCalculator.formatTime(new Date(), 'YYYY-MM-DD')
    useNudgePull({
      id: ORDERS_SUBSCRIPTION_ID,
      topics: ['orders'],
      filters: { date: todayDateStr },
      match: (ev) => ev?.type === 'nudge' && ev.topic === 'orders' && hubShouldPull({
        scope: ev.scope
      }),
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
      enterKitchenOrSettings,
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
  box-sizing: border-box;
  padding: 16px 18px 28px;
  padding-top: calc(16px + env(safe-area-inset-top));
  font-family: var(--ops-font);
  color: var(--ops-ink);
  background:
    radial-gradient(1200px 500px at 10% -10%, #d9e7f7 0%, transparent 55%),
    radial-gradient(900px 400px at 100% 0%, #e5efe7 0%, transparent 50%),
    var(--ops-bg);
}

.cockpit {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 56px);
  background: var(--ops-surface);
  border: 1px solid var(--ops-line);
  border-radius: 18px;
  box-shadow: var(--ops-shadow);
  overflow: hidden;
}

.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 14px 22px;
  border-bottom: 1px solid var(--ops-line);
  background: #fbfcfe;
}

.brand {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--ops-ink);
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 14px;
}

.nav-link-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.nav-link {
  font-size: 14px;
  color: var(--ops-muted);
  font-weight: 500;
}

.nav-badge {
  position: absolute;
  top: -10px;
  right: -14px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--ops-danger);
  display: flex;
  align-items: center;
  justify-content: center;
}

.nav-badge-text {
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.cockpit-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.overview-pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  background: #f7f9fc;
  border-bottom: 1px solid var(--ops-line);
}

.overview-pane--alert {
  background: #fff4f4;
}

.status-pill {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--ops-ok-soft);
}

.status-pill.status-online .status-text {
  color: var(--ops-ok);
}

.status-pill.status-error,
.status-pill.status-offline {
  background: var(--ops-danger-soft);
}

.status-pill.status-error .status-text,
.status-pill.status-offline .status-text {
  color: var(--ops-danger);
}

.indicator-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.indicator-dot.status-online {
  background: var(--ops-ok);
  box-shadow: 0 0 8px rgba(31, 138, 76, 0.45);
}

.indicator-dot.status-error {
  background: var(--ops-danger);
  animation: blink 1s infinite;
}

.indicator-dot.status-offline {
  background: var(--ops-danger);
}

.status-text {
  font-size: 12px;
  font-weight: 600;
  color: var(--ops-ink);
}

.overview-metric-label {
  display: block;
  font-size: 12px;
  color: var(--ops-muted);
  margin-bottom: 4px;
}

.overview-metric-value {
  display: block;
  font-size: 48px;
  font-weight: 800;
  line-height: 1;
  color: var(--ops-ink);
}

.overview-metric-value.urgent {
  color: var(--ops-danger);
  font-size: 28px;
}

.overview-metric--dim {
  opacity: 0.45;
}

.overview-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.overview-box {
  background: #fff;
  border: 1px solid var(--ops-line);
  border-radius: 12px;
  padding: 12px;
}

.refresh-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.refresh-label {
  font-size: 12px;
  color: var(--ops-accent);
  font-weight: 600;
}

.refresh-icon {
  transition: transform 0.6s ease;
}

.refresh-icon.rotating {
  animation: spin 1s linear infinite;
}

.alert-hint {
  font-size: 13px;
  font-weight: 600;
  color: var(--ops-danger);
  line-height: 1.4;
}

.scope-hint {
  font-size: 13px;
  color: var(--ops-muted);
}

.cta-btn {
  margin-top: auto;
  background: var(--ops-accent);
  border-radius: 10px;
  padding: 14px 18px;
}

.cta-btn:active {
  opacity: 0.92;
}

.cta-title {
  display: block;
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
}

.cta-desc {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.88);
}

.stations-pane {
  flex: 1;
  padding: 18px 20px 20px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 14px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--ops-ink);
}

.section-desc {
  font-size: 13px;
  color: var(--ops-muted);
}

.station-mosaic {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.station-card {
  background: linear-gradient(180deg, #fff, #f7fafc);
  border: 1px solid var(--ops-line);
  border-radius: var(--ops-radius);
  padding: 16px;
  min-height: 120px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.station-card--locked {
  border-color: var(--ops-accent);
  box-shadow: 0 0 0 1px var(--ops-accent);
}

.station-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.station-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.station-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--ops-ink);
}

.station-lock-mark {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: var(--ops-accent);
  background: #e8f2fb;
}

.station-stats {
  display: flex;
  gap: 18px;
}

.station-stats--secondary {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--ops-line);
}

.station-stats--secondary .station-stat-value {
  font-size: 14px;
  font-weight: 700;
  color: var(--ops-muted);
}

.station-stats--secondary .station-stat-label {
  font-size: 11px;
}

.station-stat-value {
  display: block;
  font-size: 22px;
  font-weight: 700;
  color: var(--ops-ink);
  line-height: 1.1;
}

.station-stat-label {
  display: block;
  font-size: 11px;
  color: var(--ops-muted);
  margin-top: 2px;
}

.station-stat.hot .station-stat-value {
  color: var(--ops-warn);
}

.station-stat.urgent .station-stat-value {
  color: var(--ops-danger);
}

.empty-stations {
  padding: 48px 0;
  text-align: center;
}

.empty-text {
  font-size: 13px;
  color: var(--ops-muted);
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0.3; }
}

@media screen and (min-width: 1200px) {
  .dashboard-page {
    padding: 18px 24px 28px;
  }

  .cockpit {
    min-height: calc(100vh - 56px);
  }

  .cockpit-body {
    flex-direction: row;
    min-height: 640px;
  }

  .overview-pane {
    width: 340px;
    flex-shrink: 0;
    border-bottom: none;
    border-right: 1px solid var(--ops-line);
  }

  .station-mosaic {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media screen and (min-width: 1400px) {
  .station-mosaic {
    grid-template-columns: repeat(3, 1fr);
  }

  .station-card {
    min-height: 140px;
  }

  .station-stat-value {
    font-size: 28px;
  }
}
</style>
