<script setup>
import { computed } from 'vue'

const props = defineProps({
  summary: { type: Object, default: null },
  topDish: { type: Object, default: null },
  refundCount: { type: [Number, String], default: '--' },
  revenueCompareSub: { type: String, default: '' },
  revenueCompareTrend: { type: String, default: '' },
})
defineEmits(['open-refunds'])

// 订单数 = 账单数（total_bills）；旧接口没有该字段时退回行数，避免整页显示 "--"。
const bills = computed(() => Number(props.summary?.total_bills ?? props.summary?.total_orders ?? 0))
const avgPerBill = computed(() => (bills.value ? Number(props.summary?.total_revenue || 0) / bills.value : null))

function num(v) {
  return v ?? '--'
}

// R41：金额/客单价保留小数（¥20822.7 而不是 ¥20823），顺带千分位。
function money(v) {
  return Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}
</script>

<template>
  <div class="summary-row">
    <div class="summary-card">
      <div class="label">订单数</div>
      <div class="value">{{ summary ? bills : '--' }}</div>
    </div>
    <div class="summary-card">
      <div class="label">菜件总数</div>
      <div class="value">{{ num(summary?.total_dishes) }}</div>
    </div>
    <div class="summary-card">
      <div class="label">菜品数</div>
      <div class="value">{{ num(summary?.unique_dishes) }}</div>
    </div>
    <div class="summary-card">
      <div class="label">规则覆盖</div>
      <div class="value">{{ summary?.unique_dishes ? Math.round((summary.covered_rules / summary.unique_dishes) * 100) : 0 }}%</div>
      <div class="sub">有换算规则的菜品数</div>
    </div>
    <div class="summary-card">
      <div class="label">销售金额</div>
      <div class="value">¥{{ money(summary?.total_revenue) }}</div>
      <div class="sub" :class="revenueCompareTrend === 'up' ? 'trend-up' : (revenueCompareTrend === 'down' ? 'trend-down' : '')">
        {{ revenueCompareSub }}
      </div>
    </div>
    <div class="summary-card">
      <div class="label">客单价</div>
      <div class="value">{{ avgPerBill === null ? '--' : `¥${money(avgPerBill)}` }}</div>
      <div class="sub">销售额 / 账单数</div>
    </div>
    <div class="summary-card">
      <div class="label">销冠菜品</div>
      <div class="value">{{ topDish?.dish_name || '--' }}</div>
      <div class="sub">{{ topDish ? `${topDish.qty} 份` : '销量最高' }}</div>
    </div>
    <div class="summary-card clickable" title="查看退菜明细" @click="$emit('open-refunds')">
      <div class="label">退菜行数</div>
      <div class="value">{{ refundCount }}</div>
      <div class="sub">点击查看明细</div>
    </div>
  </div>
</template>

<style scoped>
/* R51：8 张卡的副文案行数不同，统一高度避免 390 下行高 61/77/77/82 参差。 */
.summary-card {
  min-height: 74px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

/* R50：834 下 5+3 会留下半行空白，固定成 4×2。 */
@media (max-width: 900px) and (min-width: 600px) {
  .summary-row {
    grid-template-columns: repeat(4, 1fr);
  }
}
</style>
