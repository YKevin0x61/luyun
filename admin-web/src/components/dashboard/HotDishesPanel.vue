<script setup>
import { useStationsStore } from '../../stores/stations'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  dishes: { type: Array, default: () => [] },
})
const stationsStore = useStationsStore()

// 前三名金/银/铜牌配色，对齐老页 public/index.html 的 rankCls（.hot-rank.gold/.silver/.bronze），第4名起保持普通序号色。
// 铜牌走 `--rank-bronze`（theme.css）而不是字面量：旧的 #b45309 压在卡片底 #1a2035 上
// 只有 3.21:1，低于 AA —— 换成同色系的 #d97706（5.06:1）后仍是铜色、与金银同一档亮度。
const RANK_COLORS = ['#fbbf24', '#9ca3af', 'var(--rank-bronze)']
function rankColor(idx) {
  return RANK_COLORS[idx] || 'var(--text-dim)'
}
</script>

<template>
  <div class="card">
    <div class="panel-title"><SvgIcon name="flame" :size="16" /> 热销 TOP {{ dishes.length }}</div>
    <div v-if="!dishes.length" class="empty-state">暂无数据</div>
    <div v-else style="display:flex;flex-direction:column;gap:6px;max-height:360px;overflow-y:auto" class="luyun-scrollbar">
      <div
        v-for="(dish, idx) in dishes"
        :key="dish.dish_name + dish.station"
        style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;background:var(--card2)"
      >
        <span style="width:18px;text-align:center;font-weight:700" :style="{ color: rankColor(idx) }">{{ idx + 1 }}</span>
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ dish.dish_name }}</span>
        <span class="badge">{{ stationsStore.nameOf(dish.station) }}</span>
        <span style="font-weight:700;color:var(--accent)">×{{ dish.total_quantity }}</span>
      </div>
    </div>
  </div>
</template>
