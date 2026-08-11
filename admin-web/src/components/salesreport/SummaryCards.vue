<script setup>
const props = defineProps({
  summary: { type: Object, default: null },
  topDish: { type: Object, default: null },
  refundCount: { type: [Number, String], default: '--' },
  revenueCompareSub: { type: String, default: '' },
  revenueCompareTrend: { type: String, default: '' },
})
defineEmits(['open-refunds'])

function num(v) {
  return v ?? '--'
}
</script>

<template>
  <div class="summary-row">
    <div class="summary-card">
      <div class="label">订单数</div>
      <div class="value">{{ num(summary?.total_orders) }}</div>
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
      <div class="sub">有换算规则的菜件数</div>
    </div>
    <div class="summary-card">
      <div class="label">销售金额</div>
      <div class="value">¥{{ Number(summary?.total_revenue || 0).toFixed(0) }}</div>
      <div class="sub" :class="revenueCompareTrend === 'up' ? 'trend-up' : (revenueCompareTrend === 'down' ? 'trend-down' : '')">
        {{ revenueCompareSub }}
      </div>
    </div>
    <div class="summary-card">
      <div class="label">客单价</div>
      <div class="value">
        {{ summary?.total_orders ? `¥${(Number(summary.total_revenue || 0) / Number(summary.total_orders)).toFixed(0)}` : '--' }}
      </div>
      <div class="sub">销售额 / 订单数</div>
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
