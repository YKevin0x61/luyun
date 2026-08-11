<script setup>
import { computed } from 'vue'
import SvgIcon from '../SvgIcon.vue'
import { useStationsStore } from '../../stores/stations'

const props = defineProps({
  summary: { type: Object, default: null },
})

const stationsStore = useStationsStore()

// 图标 + 配色对齐旧 public/index.html 统计卡片区（L1127-1168）的 data-icon/data-icon-color。
const STAT_ICONS = {
  orders: { name: 'clipboard', bg: 'rgba(99,102,241,0.15)', color: '#818cf8' },
  revenue: { name: 'banknote', bg: 'rgba(34,197,94,0.12)', color: '#4ade80' },
  quantity: { name: 'utensils', bg: 'rgba(6,182,212,0.12)', color: '#22d3ee' },
  dishCategory: { name: 'tag', bg: 'rgba(139,92,246,0.12)', color: '#a78bfa' },
  kitchenHealth: { name: 'siren', bg: 'rgba(239,68,68,0.12)', color: '#f87171' },
  tables: { name: 'armchair', bg: 'rgba(236,72,153,0.12)', color: '#f472b6' },
  scraper: { name: 'brain', bg: 'rgba(245,158,11,0.12)', color: '#fbbf24' },
}

const scraperState = computed(() => {
  const scraper = props.summary?.scraper
  const dq = props.summary?.data_quality
  const missPct = dq?.last_reconcile?.miss_rate_pct
  const hasMissAlert = missPct != null && missPct > 0

  if (scraper?.paused) {
    return { cls: 'yellow', text: '已暂停' }
  }
  if (hasMissAlert) {
    return { cls: 'yellow', text: `漏抓 ${missPct.toFixed(2)}%` }
  }
  if ((dq?.api_failures ?? 0) > 0) {
    return { cls: 'yellow', text: 'API 异常' }
  }
  if (scraper?.status === 'running') {
    return { cls: 'green', text: '运行中' }
  }
  return { cls: 'red', text: '未运行' }
})

const kitchenHealth = computed(() => {
  const backlog = props.summary?.kds_backlog
  const busiest = backlog?.busiest_station
  return {
    pending: backlog?.total_pending ?? 0,
    overdue: backlog?.overdue_count ?? 0,
    busiestLabel: busiest?.station_id
      ? `${stationsStore.nameOf(busiest.station_id)} ${busiest.pending} 份`
      : '—',
  }
})
</script>

<template>
  <div class="grid grid-stats">
    <div class="card stat-card">
      <div class="stat-icon" :style="{ background: STAT_ICONS.orders.bg, color: STAT_ICONS.orders.color }">
        <SvgIcon :name="STAT_ICONS.orders.name" :size="22" />
      </div>
      <div class="stat-info">
        <span class="label">今日订单</span>
        <span class="value accent">{{ summary?.orders?.total_orders ?? '—' }}</span>
      </div>
    </div>
    <div class="card stat-card">
      <div class="stat-icon" :style="{ background: STAT_ICONS.revenue.bg, color: STAT_ICONS.revenue.color }">
        <SvgIcon :name="STAT_ICONS.revenue.name" :size="22" />
      </div>
      <div class="stat-info">
        <span class="label">今日营业额</span>
        <span class="value green">¥{{ (summary?.orders?.total_revenue ?? 0).toFixed(0) }}</span>
      </div>
    </div>
    <div class="card stat-card">
      <div class="stat-icon" :style="{ background: STAT_ICONS.quantity.bg, color: STAT_ICONS.quantity.color }">
        <SvgIcon :name="STAT_ICONS.quantity.name" :size="22" />
      </div>
      <div class="stat-info">
        <span class="label">出品数量</span>
        <span class="value">{{ summary?.orders?.total_quantity ?? '—' }}</span>
      </div>
    </div>
    <div class="card stat-card">
      <div class="stat-icon" :style="{ background: STAT_ICONS.dishCategory.bg, color: STAT_ICONS.dishCategory.color }">
        <SvgIcon :name="STAT_ICONS.dishCategory.name" :size="22" />
      </div>
      <div class="stat-info">
        <span class="label">菜品分类数</span>
        <span class="value">{{ summary?.dashboard?.dish_category_count ?? '—' }}</span>
      </div>
    </div>
    <div class="card stat-card">
      <div class="stat-icon" :style="{ background: STAT_ICONS.kitchenHealth.bg, color: STAT_ICONS.kitchenHealth.color }">
        <SvgIcon :name="STAT_ICONS.kitchenHealth.name" :size="22" />
      </div>
      <div class="stat-info">
        <span class="label">出餐健康</span>
        <span class="value" :class="kitchenHealth.overdue > 0 ? 'red' : (kitchenHealth.pending > 0 ? 'yellow' : '')">
          待出 {{ kitchenHealth.pending }} · 超时 {{ kitchenHealth.overdue }}
        </span>
        <span class="sub-label">最忙 {{ kitchenHealth.busiestLabel }}</span>
      </div>
    </div>
    <div class="card stat-card">
      <div class="stat-icon" :style="{ background: STAT_ICONS.tables.bg, color: STAT_ICONS.tables.color }">
        <SvgIcon :name="STAT_ICONS.tables.name" :size="22" />
      </div>
      <div class="stat-info">
        <span class="label">餐桌占用</span>
        <span class="value yellow">{{ summary?.dashboard?.table_occupancy?.occupied ?? 0 }}</span>
        <span class="sub-label">占用率 {{ summary?.dashboard?.table_occupancy?.percent ?? 0 }}%</span>
      </div>
    </div>
    <div class="card stat-card">
      <div class="stat-icon" :style="{ background: STAT_ICONS.scraper.bg, color: STAT_ICONS.scraper.color }">
        <SvgIcon :name="STAT_ICONS.scraper.name" :size="22" />
      </div>
      <div class="stat-info">
        <span class="label">采集状态</span>
        <span class="value" :class="scraperState.cls">{{ scraperState.text }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 覆盖全局纵向 .stat-card 布局为图标+信息横排，对齐旧 public/index.html 的统计卡片结构。 */
.grid-stats .stat-card {
  flex-direction: row;
  align-items: center;
  gap: 12px;
}
.stat-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
/* 餐桌占用副文案：主数值对齐老页"占用几桌"绝对值口径，占用率百分比作为补充展示。 */
.sub-label {
  font-size: 11px;
  color: var(--text-dim);
}
</style>
