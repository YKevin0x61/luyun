<script setup>
import { computed, ref } from 'vue'
import { useStationsStore } from '../../stores/stations'
import SvgIcon from '../SvgIcon.vue'
import LuyunCheckbox from '../ui/LuyunCheckbox.vue'
import LuyunNumberInput from '../ui/LuyunNumberInput.vue'

const props = defineProps({
  dishes: { type: Array, default: () => [] },
})
const stationsStore = useStationsStore()

const keyword = ref('')
const onlyUncovered = ref(false)
const sortBy = ref('qty')
const minQty = ref('')

const totalAmount = computed(() => props.dishes.reduce((s, d) => s + Number(d.total_amount || 0), 0))

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const min = Number(minQty.value || 0)
  let list = props.dishes.filter((d) => {
    const label = stationsStore.nameOf(d.station) || ''
    const matchesKw = !kw || `${d.dish_name} ${label}`.toLowerCase().includes(kw)
    const matchesQty = !min || Number(d.qty || 0) >= min
    const matchesCoverage = !onlyUncovered.value || !d.has_rules
    return matchesKw && matchesQty && matchesCoverage
  })
  list = [...list].sort((a, b) => {
    if (sortBy.value === 'amount') return Number(b.total_amount || 0) - Number(a.total_amount || 0)
    if (sortBy.value === 'name') return String(a.dish_name).localeCompare(String(b.dish_name), 'zh-Hans-CN')
    if (sortBy.value === 'station') return String(a.station || '').localeCompare(String(b.station || ''), 'zh-Hans-CN')
    return Number(b.qty || 0) - Number(a.qty || 0)
  })
  return list
})
</script>

<template>
  <div class="table-card">
    <div class="table-card-header">
      <h3><SvgIcon name="utensils" :size="15" /> 菜品销量 <span>{{ filtered.length }}/{{ dishes.length }}</span></h3>
      <div class="table-tools">
        <input class="input" v-model="keyword" placeholder="搜索菜品...">
        <label class="luyun-check-row" style="font-size:11px">
          <LuyunCheckbox v-model="onlyUncovered" /> 未覆盖
        </label>
        <select class="select" v-model="sortBy">
          <option value="qty">按数量</option>
          <option value="amount">按金额</option>
          <option value="name">按名称</option>
          <option value="station">按档口</option>
        </select>
        <LuyunNumberInput v-model="minQty" compact :min="0" placeholder="最低数" />
      </div>
    </div>
    <div style="flex:1;overflow-y:auto" class="luyun-scrollbar">
      <table class="data-table">
        <thead>
          <tr>
            <th>#</th><th>菜品名称</th><th>档口</th>
            <th style="text-align:right">数量</th>
            <th style="text-align:right">金额</th>
            <th style="text-align:right">占比</th>
            <th style="text-align:right">均价</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!filtered.length"><td colspan="7" class="empty-state">暂无数据</td></tr>
          <tr v-for="(d, i) in filtered" :key="d.dish_name + d.station">
            <td><span class="badge">{{ i + 1 }}</span></td>
            <td>{{ d.dish_name }}</td>
            <td><span class="badge">{{ stationsStore.nameOf(d.station) || '-' }}</span></td>
            <td style="text-align:right">{{ d.qty }}</td>
            <td style="text-align:right">¥{{ Number(d.total_amount || 0).toFixed(2) }}</td>
            <td style="text-align:right">{{ totalAmount ? (Number(d.total_amount || 0) / totalAmount * 100).toFixed(1) : '0.0' }}%</td>
            <td style="text-align:right">¥{{ Number(d.qty || 0) ? (Number(d.total_amount || 0) / Number(d.qty || 0)).toFixed(2) : '0.00' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
