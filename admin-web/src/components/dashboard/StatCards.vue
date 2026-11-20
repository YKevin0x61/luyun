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

// KDS 数据源健康（后端 kds_backlog.kds_active）：营业日窗口内「已制作待上菜」行数占比
// 不足 2% 说明 dish_status 没人回写，待出/超时数字全是失真告警，卡片降级成「KDS 未启用」。
// 旧接口没有该字段时按 true 处理，保持原有红色告警行为。
const kdsActive = computed(() => props.summary?.kds_backlog?.kds_active !== false)

// KDS 未启用时图标也换成中性橙，不再用告警红。直接复用「采集状态」那组色（STAT_ICONS.scraper），
// 不新增裸 hex/rgba：设计护栏按文件数存量色值，只减不增。
const kitchenHealthIcon = computed(() => (kdsActive.value
  ? STAT_ICONS.kitchenHealth
  : { name: STAT_ICONS.kitchenHealth.name, bg: STAT_ICONS.scraper.bg, color: STAT_ICONS.scraper.color }))
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
      <div class="stat-icon" :style="{ background: kitchenHealthIcon.bg, color: kitchenHealthIcon.color }">
        <SvgIcon :name="kitchenHealthIcon.name" :size="22" />
      </div>
      <div class="stat-info">
        <span class="label">出餐健康</span>
        <template v-if="kdsActive">
          <span class="value" :class="kitchenHealth.overdue > 0 ? 'red' : (kitchenHealth.pending > 0 ? 'yellow' : '')">
            待出 {{ kitchenHealth.pending }} · 超时 {{ kitchenHealth.overdue }}
          </span>
          <span class="sub-label">最忙 {{ kitchenHealth.busiestLabel }}</span>
        </template>
        <!-- KDS 没回写 dish_status 时待出/超时不可信：橙色提示 + 去开启入口，不报红 -->
        <template v-else>
          <span class="value yellow value-text">未启用</span>
          <span class="sub-label">KDS 未回写 · 待出 {{ kitchenHealth.pending }} 条</span>
        </template>
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
/* KDS 未启用时 value 从 22px 的大数字降成一句短话：22px 下「KDS 未启用」在 160px 卡里会折成
   「KDS 未启 / 用」两行，还把整张卡顶高、和旁边六张错位。入口仍在下方的「厨房 KDS」大卡。 */
.value-text {
  font-size: 16px;
  line-height: 1.4;
}
</style>
