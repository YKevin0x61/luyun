<script setup>
import { computed } from 'vue'
import SvgIcon from '../components/SvgIcon.vue'
import StatCards from '../components/dashboard/StatCards.vue'
import HotDishesPanel from '../components/dashboard/HotDishesPanel.vue'
import RecentOrdersPanel from '../components/dashboard/RecentOrdersPanel.vue'
import StationSpeedChart from '../components/dashboard/StationSpeedChart.vue'
import KdsBacklogPanel from '../components/dashboard/KdsBacklogPanel.vue'
import TableLivePanel from '../components/dashboard/TableLivePanel.vue'
import SystemAlertBanner from '../components/dashboard/SystemAlertBanner.vue'
import { useDashboardData } from '../composables/useDashboardData'

const { summary, loading, error, refresh } = useDashboardData()

const hotDishes = computed(() => summary.value?.hot_dishes || [])
const recentOrders = computed(() => summary.value?.recent_orders || [])
const kdsBacklog = computed(() => summary.value?.kds_backlog || null)

const lastUpdateText = computed(() => {
  if (!summary.value?.timestamp) return '—'
  return new Date(summary.value.timestamp).toLocaleTimeString('zh-CN', { hour12: false })
})
</script>

<template>
  <div>
    <div v-if="loading && !summary" class="loading-state">加载仪表盘中...</div>
    <div v-else-if="error && !summary" class="empty-state">{{ error }}</div>
    <div v-else style="display:flex;flex-direction:column;gap:14px">
      <div v-if="error" class="dash-error-banner"><SvgIcon name="alert-triangle" :size="14" /> 仪表盘数据可能已过期：{{ error }}</div>
      <SystemAlertBanner :summary="summary" />
      <div class="grid" style="grid-template-columns: repeat(2, minmax(0, 1fr))">
        <router-link to="/recipe" class="card app-shortcut">
          <span class="app-shortcut-title">配方 SOP</span>
          <span class="app-shortcut-desc">岗位配方 · 出品检核</span>
        </router-link>
        <a href="/kds/" class="card app-shortcut">
          <span class="app-shortcut-title">厨房 KDS</span>
          <span class="app-shortcut-desc">后厨控菜 · 制作进度</span>
        </a>
      </div>
      <StatCards :summary="summary" />
      <div class="grid grid-2col">
        <StationSpeedChart />
        <KdsBacklogPanel :backlog="kdsBacklog" />
      </div>
      <div class="grid grid-2col">
        <HotDishesPanel :dishes="hotDishes" />
        <TableLivePanel />
      </div>
      <RecentOrdersPanel :orders="recentOrders" />
      <div class="dash-footer">
        <button type="button" class="btn btn-sm" :disabled="loading" @click="refresh">立即刷新</button>
        <span>最后更新：{{ lastUpdateText }}</span>
      </div>
    </div>
  </div>
</template>
