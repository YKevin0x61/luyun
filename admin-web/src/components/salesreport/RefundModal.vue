<script setup>
import { useStationsStore } from '../../stores/stations'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
})
const emit = defineEmits(['close'])
const stationsStore = useStationsStore()
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-box" style="width:520px">
      <div class="modal-header">
        <h3>退菜明细</h3>
        <button class="btn btn-sm" @click="emit('close')"><SvgIcon name="x" :size="14" /></button>
      </div>
      <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">
        数量为负数或状态包含「退」的订单行，按菜品和档口汇总。
      </p>
      <div style="max-height:360px;overflow-y:auto;border:1px solid var(--border);border-radius:8px" class="luyun-scrollbar">
        <div v-if="!items.length" class="empty-state">暂无退菜明细</div>
        <div
          v-for="(item, i) in items"
          :key="i"
          style="display:grid;grid-template-columns:1fr 78px 64px;gap:8px;padding:8px 10px;border-bottom:1px solid var(--border);font-size:12px"
        >
          <div>{{ item.dish_name || '-' }}</div>
          <div><span class="badge">{{ stationsStore.nameOf(item.station) }}</span></div>
          <div style="text-align:right">{{ item.qty ?? 0 }} / {{ item.cnt ?? 0 }}行</div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>
