<script setup>
import { computed, ref } from 'vue'
import { api } from '../../api/client'
import { useStationsStore } from '../../stores/stations'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  orders: { type: Array, default: () => [] },
})
const stationsStore = useStationsStore()

// 与旧 public/index.html loadRecentOrders()/renderRecentOrders() 一致（L1701-1799）：
// 拉取时限 30 条，展示时截断 20 条。
const RECENT_ORDERS_FETCH_LIMIT = 30
const RECENT_ORDERS_DISPLAY_LIMIT = 20
const LOUMIAN_STATION_ID = 'loumian'

const stationOptions = computed(() => stationsStore.list.filter((s) => s.id !== LOUMIAN_STATION_ID))

const stationFilter = ref('')
const tableFilter = ref('')
const filteredOrders = ref(null)
const filterLoading = ref(false)
const filterError = ref('')
const expandedKey = ref('')

const hasActiveFilter = computed(() => !!stationFilter.value || !!tableFilter.value.trim())

const displayOrders = computed(() => {
  const source = hasActiveFilter.value ? filteredOrders.value || [] : props.orders
  return source.slice(0, RECENT_ORDERS_DISPLAY_LIMIT)
})

async function applyFilters() {
  if (!hasActiveFilter.value) {
    filteredOrders.value = null
    filterError.value = ''
    return
  }
  filterLoading.value = true
  filterError.value = ''
  try {
    const res = await api.get('/api/orders/', {
      limit: RECENT_ORDERS_FETCH_LIMIT,
      station: stationFilter.value || undefined,
      table_number: tableFilter.value.trim() || undefined,
    })
    filteredOrders.value = res.data || []
  } catch (e) {
    filterError.value = e.message || '订单流加载失败'
    filteredOrders.value = []
  } finally {
    filterLoading.value = false
  }
}

function clearFilters() {
  stationFilter.value = ''
  tableFilter.value = ''
  filteredOrders.value = null
  filterError.value = ''
}

function orderKey(order, index) {
  return String(order.business_flow_id || order._id || `row-${index}`)
}

function toggleDetail(order, index) {
  const key = orderKey(order, index)
  expandedKey.value = expandedKey.value === key ? '' : key
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<template>
  <div class="card">
    <div class="panel-title" style="justify-content:space-between">
      <span><SvgIcon name="clipboard" :size="16" /> 最新订单</span>
      <span class="badge">{{ displayOrders.length }} 条</span>
    </div>

    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
      <select class="select" v-model="stationFilter" @change="applyFilters" style="max-width:130px" aria-label="按档口筛选">
        <option value="">全部档口</option>
        <option v-for="s in stationOptions" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <input
        class="input"
        v-model="tableFilter"
        placeholder="桌号"
        style="max-width:90px"
        aria-label="按桌号筛选"
        @keydown.enter="applyFilters"
      />
      <button type="button" class="btn btn-sm" @click="applyFilters">筛选</button>
      <button type="button" class="btn btn-sm" :disabled="!hasActiveFilter" @click="clearFilters">清除</button>
    </div>

    <div v-if="filterError" class="empty-state">{{ filterError }}</div>
    <div v-else-if="!displayOrders.length" class="empty-state">暂无订单</div>
    <!-- key 用 order._id/business_flow_id，Vue 会自动做最小化 DOM diff -->
    <div v-else style="display:flex;flex-direction:column;gap:6px;max-height:360px;overflow-y:auto" class="luyun-scrollbar">
      <div
        v-for="(order, index) in displayOrders"
        :key="orderKey(order, index)"
        role="button"
        tabindex="0"
        :aria-expanded="expandedKey === orderKey(order, index)"
        style="display:flex;flex-direction:column;gap:4px;padding:6px 8px;border-radius:8px;background:var(--card2);font-size:12px;cursor:pointer"
        @click="toggleDetail(order, index)"
        @keydown.enter="toggleDetail(order, index)"
        @keydown.space.prevent="toggleDetail(order, index)"
      >
        <div style="display:flex;align-items:center;gap:8px">
          <span style="color:var(--text-dim);width:64px">{{ formatTime(order.order_time) }}</span>
          <span style="width:44px">桌{{ order.table_number }}</span>
          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ order.dish_name }}</span>
          <span class="badge">{{ stationsStore.nameOf(order.station) }}</span>
          <span style="color:var(--text-dim)">×{{ order.quantity }}</span>
        </div>
        <div
          v-if="expandedKey === orderKey(order, index)"
          style="font-size:11px;color:var(--text-dim);padding-top:4px;border-top:1px dashed var(--border)"
        >
          流水号: {{ order.business_flow_id || '—' }} · 分类: {{ order.category || '—' }} · 单价: ¥{{ order.price ?? '—' }} · 优先级: {{ order.priority || 'normal' }}
        </div>
      </div>
    </div>
  </div>
</template>
