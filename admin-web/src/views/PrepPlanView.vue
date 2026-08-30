<script setup>
import { computed, onMounted } from 'vue'
import { usePrepPlan } from '../composables/usePrepPlan'
import { useStationsStore } from '../stores/stations'
import {
  EMPTY_HINT,
  FRESH_AVAILABLE_LABEL,
  NEAR_AVAILABLE_LABEL,
  PREP_PLAN_TITLE,
  RECOMMENDED_LABEL,
  REFRESH_LABEL,
  SKIP_LABEL,
  TODO_COUNT_LABEL,
  UNCATEGORIZED_STATION,
} from '../utils/prepPlanCopy'

const LOUMIAN_STATION = 'loumian'

const {
  busy,
  statusText,
  errorText,
  items,
  missingRules,
  lowConfidence,
  refresh,
} = usePrepPlan()

const stationsStore = useStationsStore()
onMounted(() => stationsStore.load())

const kitchenItems = computed(() =>
  items.value.filter((item) => (item.station || '').trim() !== LOUMIAN_STATION)
)

const todoCount = computed(
  () => kitchenItems.value.filter((item) => Number(item.recommended_qty || 0) > 0).length
)

const board = computed(() => {
  const grouped = new Map()
  for (const item of kitchenItems.value) {
    const stationId = (item.station || '').trim() || UNCATEGORIZED_STATION
    if (!grouped.has(stationId)) grouped.set(stationId, [])
    grouped.get(stationId).push(item)
  }
  const stationOrder = stationsStore.list
    .filter((station) => station.id && station.id !== LOUMIAN_STATION)
    .map((station) => station.id)
  const keys = Array.from(grouped.keys()).sort((a, b) => {
    const ai = a === UNCATEGORIZED_STATION ? 1000 : stationOrder.indexOf(a)
    const bi = b === UNCATEGORIZED_STATION ? 1000 : stationOrder.indexOf(b)
    const ar = ai === -1 ? 999 : ai
    const br = bi === -1 ? 999 : bi
    if (ar !== br) return ar - br
    return a.localeCompare(b)
  })
  return keys.map((stationId) => ({
    stationId,
    label: stationId === UNCATEGORIZED_STATION ? UNCATEGORIZED_STATION : stationsStore.nameOf(stationId),
    items: [...grouped.get(stationId)].sort(
      (a, b) => Number(b.recommended_qty || 0) - Number(a.recommended_qty || 0)
    ),
  }))
})

function qtyText(value) {
  return Number.isFinite(Number(value)) ? String(Math.round(Number(value))) : '0'
}
</script>

<template>
  <div class="prep-plan">
    <div class="card prep-toolbar">
      <h1 class="prep-title">{{ PREP_PLAN_TITLE }}</h1>
      <div class="prep-toolbar-row">
        <button
          type="button"
          class="btn btn-primary prep-refresh"
          :disabled="busy"
          @click="refresh"
        >
          {{ REFRESH_LABEL }}
        </button>
        <span class="prep-status" :class="{ 'is-error': errorText }">{{ errorText || statusText }}</span>
      </div>
    </div>

    <div v-if="!board.length" class="card prep-empty">
      <p class="empty-state">{{ errorText ? errorText : EMPTY_HINT }}</p>
    </div>

    <div v-else class="prep-board">
      <p class="prep-todo">{{ TODO_COUNT_LABEL }} {{ todoCount }} 项</p>
      <section v-for="group in board" :key="group.stationId" class="card prep-station">
        <h2 class="prep-station-title">{{ group.label }}</h2>
        <ul class="prep-rows">
          <li v-for="item in group.items" :key="`${item.item_name}|${item.unit}`" class="prep-row">
            <div class="prep-row-main">
              <div class="prep-item-name">{{ item.item_name }}</div>
              <div class="prep-rec">
                <span class="prep-rec-label">{{ RECOMMENDED_LABEL }}</span>
                <span class="prep-rec-qty">{{ qtyText(item.recommended_qty) }}</span>
                <span class="prep-rec-unit">{{ item.unit }}</span>
                <span v-if="Number(item.recommended_qty || 0) <= 0" class="prep-skip">{{ SKIP_LABEL }}</span>
              </div>
            </div>
            <div class="prep-avail">
              <span>{{ FRESH_AVAILABLE_LABEL }} {{ qtyText(item.available_fresh_qty) }} {{ item.unit }}</span>
              <span>{{ NEAR_AVAILABLE_LABEL }} {{ qtyText(item.available_near_expiry_qty) }} {{ item.unit }}</span>
            </div>
          </li>
        </ul>
      </section>
    </div>

    <details
      v-if="lowConfidence.length || missingRules.length"
      class="card prep-aux"
    >
      <summary>样本不足 {{ lowConfidence.length }} · 缺规则 {{ missingRules.length }}</summary>
      <ul v-if="lowConfidence.length" class="prep-aux-list">
        <li v-for="(row, index) in lowConfidence" :key="`low-${index}`">
          {{ row.item_name }}（{{ row.unit || '—' }}）{{ row.reason || '' }}
        </li>
      </ul>
      <ul v-if="missingRules.length" class="prep-aux-list">
        <li v-for="(row, index) in missingRules" :key="`miss-${index}`">
          {{ row.dish_name }} {{ row.reason || '' }}
        </li>
      </ul>
    </details>
  </div>
</template>

<style scoped>
.prep-plan {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.prep-title {
  font-size: 20px;
  font-weight: 700;
  margin: 0 0 10px;
}
.prep-toolbar-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.prep-refresh {
  min-height: 44px;
  padding: 10px 18px;
  font-size: 15px;
}
.prep-status {
  font-size: 13px;
  color: var(--text-dim);
}
.prep-status.is-error {
  color: var(--red);
}
.prep-empty .empty-state {
  padding: 28px 12px;
  font-size: 14px;
}
.prep-todo {
  margin: 0 2px 4px;
  font-size: 14px;
  color: var(--text-dim);
}
.prep-board {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.prep-station-title {
  font-size: 16px;
  font-weight: 700;
  margin: 0 0 8px;
}
.prep-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.prep-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 4px;
  border-top: 1px solid var(--border);
}
.prep-row:first-child {
  border-top: 0;
}
.prep-item-name {
  font-size: 16px;
  font-weight: 600;
}
.prep-rec {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}
.prep-rec-label {
  font-size: 12px;
  color: var(--text-dim);
}
.prep-rec-qty {
  font-size: 28px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.prep-rec-unit {
  font-size: 14px;
  color: var(--text-dim);
}
.prep-skip {
  font-size: 14px;
  font-weight: 700;
  color: var(--green);
}
.prep-avail {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 14px;
  color: var(--text);
}
.prep-aux {
  font-size: 13px;
  color: var(--text-dim);
}
.prep-aux-list {
  margin: 8px 0 0;
  padding-left: 18px;
}
</style>
