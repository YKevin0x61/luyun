<script setup>
import { computed, onMounted } from 'vue'
import ConfirmDialog from '../components/admin/ConfirmDialog.vue'
import TextExportModal from '../components/salesreport/TextExportModal.vue'
import { usePrepPlan } from '../composables/usePrepPlan'
import { useStationsStore } from '../stores/stations'
import {
  ALL_STATIONS_LABEL,
  BATCHES_LABEL,
  CONFIDENCE_LABELS,
  CONFIDENCE_FIELD_LABEL,
  CUSTOM_TIME_LABEL,
  DISCARD_LABEL,
  EMPTY_HINT,
  EXPORT_HINT,
  EXPORT_LABEL,
  EXPORT_TITLE,
  EXPIRES_AT_LABEL,
  EXTRA_RECORD_LABEL,
  FORECAST_LABEL,
  FORMULA_LABEL,
  FRESH_AVAILABLE_LABEL,
  NEAR_AVAILABLE_LABEL,
  NEAR_EXPIRY_TITLE,
  NO_MASTER_REASON,
  PREP_PLAN_TITLE,
  PRESET_LABELS,
  PRODUCED_LABEL,
  RECOMMENDED_LABEL,
  RECORD_LABEL,
  REFRESH_LABEL,
  REMAINING_LABEL,
  SAFETY_LABEL,
  SKIP_LABEL,
  TODO_COUNT_LABEL,
  UNCATEGORIZED_STATION,
  UNDO_LABEL,
  discardConfirmCopy,
  confidenceLabel,
} from '../utils/prepPlanCopy'
import { buildPrepPlanText } from '../utils/prepPlanText'
import {
  PRESET_AFTERNOON,
  PRESET_CUSTOM,
  PRESET_FUTURE_24H,
  PRESET_MORNING,
  formatPrepTime,
  rowTone,
} from '../utils/prepPlanWindow'

const LOUMIAN_STATION = 'loumian'

const PRESET_CHIPS = [
  { id: PRESET_FUTURE_24H, label: PRESET_LABELS.future24 },
  { id: PRESET_MORNING, label: PRESET_LABELS.morning },
  { id: PRESET_AFTERNOON, label: PRESET_LABELS.afternoon },
]

const {
  preset,
  customStart,
  customEnd,
  showCustom,
  stationFilter,
  extraRecordOpen,
  registerQty,
  busy,
  statusText,
  errorText,
  items,
  missingRules,
  lowConfidence,
  expiring,
  discardTarget,
  windowStart,
  windowEnd,
  exportOpen,
  itemKey,
  refresh,
  recordItem,
  undoItem,
  discardBatch,
} = usePrepPlan()

function selectPreset(id) {
  preset.value = id
  showCustom.value = false
}

function toggleCustom() {
  if (showCustom.value) {
    showCustom.value = false
    return
  }
  showCustom.value = true
  preset.value = PRESET_CUSTOM
}

function stationIdOf(row) {
  return (row.station || '').trim() || UNCATEGORIZED_STATION
}

const stationsStore = useStationsStore()
onMounted(() => stationsStore.load())

const stationChips = computed(() => [
  { id: '', label: ALL_STATIONS_LABEL },
  ...stationsStore.list
    .filter((station) => station.id && station.id !== LOUMIAN_STATION)
    .map((station) => ({ id: station.id, label: station.name })),
  { id: UNCATEGORIZED_STATION, label: UNCATEGORIZED_STATION },
])

const kitchenItems = computed(() =>
  items.value.filter((item) => stationIdOf(item) !== LOUMIAN_STATION)
)

const filteredKitchenItems = computed(() => {
  const filter = stationFilter.value
  if (!filter) return kitchenItems.value
  return kitchenItems.value.filter((item) => stationIdOf(item) === filter)
})

const filteredExpiring = computed(() => {
  const kitchenExpiring = expiring.value.filter((row) => stationIdOf(row) !== LOUMIAN_STATION)
  const filter = stationFilter.value
  if (!filter) return kitchenExpiring
  return kitchenExpiring.filter((row) => stationIdOf(row) === filter)
})

const todoCount = computed(
  () => filteredKitchenItems.value.filter((item) => Number(item.recommended_qty || 0) > 0).length
)

const board = computed(() => {
  const grouped = new Map()
  for (const item of filteredKitchenItems.value) {
    const stationId = stationIdOf(item)
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

const discardCopy = computed(() => {
  const target = discardTarget.value
  if (!target) return { title: '', body: '', confirmLabel: DISCARD_LABEL }
  return discardConfirmCopy({
    ...target,
    expires_at: formatPrepTime(target.expires_at),
  })
})

const exportText = computed(() =>
  buildPrepPlanText({
    windowStart: windowStart.value,
    windowEnd: windowEnd.value,
    stations: board.value.map((group) => ({
      stationLabel: group.label,
      items: group.items,
    })),
    expiring: filteredExpiring.value,
    missingRules: missingRules.value,
  })
)

function qtyText(value) {
  return Number.isFinite(Number(value)) ? String(Math.round(Number(value))) : '0'
}

function usableBatches(item) {
  return (item.batches || []).filter((batch) => Number(batch.remaining_qty || 0) > 0)
}

function isLowSample(item) {
  return item.confidence === 'low'
}

function registerFormOpen(item) {
  if (!item.can_record) return false
  if (Number(item.recommended_qty || 0) > 0) return true
  return Boolean(extraRecordOpen[itemKey(item)])
}

function showExtraRecord(item) {
  return Boolean(item.can_record) && Number(item.recommended_qty || 0) <= 0 && !extraRecordOpen[itemKey(item)]
}

function openExtraRecord(item) {
  extraRecordOpen[itemKey(item)] = true
}

function openDiscard(batch) {
  discardTarget.value = batch
}

function cancelDiscard() {
  discardTarget.value = null
}
</script>

<template>
  <div class="prep-plan">
    <div class="card prep-toolbar">
      <h1 class="prep-title">{{ PREP_PLAN_TITLE }}</h1>
      <div class="prep-toolbar-row" role="group" aria-label="时间窗">
        <button
          v-for="chip in PRESET_CHIPS"
          :key="chip.id"
          type="button"
          class="btn prep-preset"
          :class="{ 'is-active': preset === chip.id }"
          :aria-pressed="preset === chip.id"
          @click="selectPreset(chip.id)"
        >
          {{ chip.label }}
        </button>
        <button
          type="button"
          class="btn prep-preset"
          :class="{ 'is-active': preset === PRESET_CUSTOM }"
          :aria-pressed="preset === PRESET_CUSTOM"
          :aria-expanded="showCustom"
          @click="toggleCustom"
        >
          {{ CUSTOM_TIME_LABEL }}
        </button>
      </div>
      <div v-if="showCustom" class="prep-custom-fields">
        <label class="prep-custom-field">
          开始
          <input
            v-model="customStart"
            class="input prep-datetime"
            type="datetime-local"
          >
        </label>
        <label class="prep-custom-field">
          结束
          <input
            v-model="customEnd"
            class="input prep-datetime"
            type="datetime-local"
          >
        </label>
      </div>
      <div class="prep-toolbar-row">
        <button
          type="button"
          class="btn btn-primary prep-refresh"
          :disabled="busy"
          @click="refresh"
        >
          {{ REFRESH_LABEL }}
        </button>
        <button
          type="button"
          class="btn prep-export"
          @click="exportOpen = true"
        >
          {{ EXPORT_LABEL }}
        </button>
        <span class="prep-status" :class="{ 'is-error': errorText }">{{ errorText || statusText }}</span>
      </div>
      <div class="prep-toolbar-row prep-station-chips" role="group" aria-label="档口">
        <button
          v-for="chip in stationChips"
          :key="chip.id || 'all'"
          type="button"
          class="btn prep-preset"
          :class="{ 'is-active': stationFilter === chip.id }"
          :aria-pressed="stationFilter === chip.id"
          @click="stationFilter = chip.id"
        >
          {{ chip.label }}
        </button>
      </div>
    </div>

    <section v-if="filteredExpiring.length" class="card prep-expiring" aria-label="临期批次">
      <h2 class="prep-station-title">{{ NEAR_EXPIRY_TITLE }}</h2>
      <ul class="prep-expiring-list">
        <li v-for="batch in filteredExpiring" :key="batch.batch_id" class="prep-expiring-row">
          <div class="prep-expiring-main">
            <div class="prep-item-name">{{ batch.item_name }}</div>
            <div class="prep-avail">
              <span>{{ REMAINING_LABEL }} {{ qtyText(batch.remaining_qty) }} {{ batch.unit }}</span>
              <span>{{ EXPIRES_AT_LABEL }} {{ formatPrepTime(batch.expires_at) }}</span>
            </div>
          </div>
          <button
            type="button"
            class="btn prep-discard"
            :disabled="busy"
            @click="openDiscard(batch)"
          >
            {{ DISCARD_LABEL }}
          </button>
        </li>
      </ul>
    </section>

    <div v-if="!kitchenItems.length && !filteredExpiring.length" class="card prep-empty">
      <p class="empty-state">{{ errorText ? errorText : EMPTY_HINT }}</p>
    </div>

    <div v-else-if="kitchenItems.length" class="prep-board">
      <p class="prep-todo">{{ TODO_COUNT_LABEL }} {{ todoCount }} 项</p>
      <section v-for="group in board" :key="group.stationId" class="card prep-station">
        <h2 class="prep-station-title">{{ group.label }}</h2>
        <ul class="prep-rows">
          <li v-for="item in group.items" :key="`${item.item_name}|${item.unit}`" class="prep-row" :class="`is-${rowTone(item)}`">
            <div class="prep-row-main">
              <div class="prep-item-name">
                {{ item.item_name }}
                <span v-if="isLowSample(item)" class="badge prep-sample-badge">{{ CONFIDENCE_LABELS.low }}</span>
              </div>
              <div class="prep-rec">
                <span class="prep-rec-label">{{ RECOMMENDED_LABEL }}</span>
                <span class="prep-rec-qty" :class="`is-${rowTone(item)}`">{{ qtyText(item.recommended_qty) }}</span>
                <span class="prep-rec-unit">{{ item.unit }}</span>
                <span v-if="Number(item.recommended_qty || 0) <= 0" class="prep-skip">{{ SKIP_LABEL }}</span>
              </div>
            </div>
            <div class="prep-avail">
              <span>{{ FRESH_AVAILABLE_LABEL }} {{ qtyText(item.available_fresh_qty) }} {{ item.unit }}</span>
              <span>{{ NEAR_AVAILABLE_LABEL }} {{ qtyText(item.available_near_expiry_qty) }} {{ item.unit }}</span>
              <span>{{ PRODUCED_LABEL }} {{ qtyText(item.produced_qty) }} {{ item.unit }}</span>
            </div>
            <p v-if="!item.can_record" class="prep-no-master">{{ NO_MASTER_REASON }}</p>
            <div v-else class="prep-row-actions">
              <button
                v-if="showExtraRecord(item)"
                type="button"
                class="btn prep-extra"
                :disabled="busy"
                @click="openExtraRecord(item)"
              >
                {{ EXTRA_RECORD_LABEL }}
              </button>
              <form
                v-else-if="registerFormOpen(item)"
                class="prep-register"
                @submit.prevent="recordItem(item)"
              >
                <label class="prep-qty-field">
                  这次做了
                  <input
                    v-model.number="registerQty[itemKey(item)]"
                    class="input prep-qty-input"
                    type="number"
                    step="any"
                    inputmode="decimal"
                    :disabled="busy"
                  >
                  {{ item.unit }}
                </label>
                <button type="submit" class="btn btn-primary prep-record" :disabled="busy">
                  {{ RECORD_LABEL }}
                </button>
              </form>
              <button
                v-if="item.undo_batch_id"
                type="button"
                class="btn prep-undo"
                :disabled="busy"
                @click="undoItem(item)"
              >
                {{ UNDO_LABEL }}
              </button>
            </div>
            <details class="prep-row-more">
              <summary>{{ FORMULA_LABEL }}</summary>
              <dl class="prep-formula">
                <div>
                  <dt>{{ FORECAST_LABEL }}</dt>
                  <dd>{{ qtyText(item.forecast_qty) }} {{ item.unit }}</dd>
                </div>
                <div>
                  <dt>{{ SAFETY_LABEL }}</dt>
                  <dd>{{ qtyText(item.safety_qty) }} {{ item.unit }}</dd>
                </div>
                <div>
                  <dt>{{ FRESH_AVAILABLE_LABEL }}</dt>
                  <dd>{{ qtyText(item.available_fresh_qty) }} {{ item.unit }}</dd>
                </div>
                <div>
                  <dt>{{ NEAR_AVAILABLE_LABEL }}</dt>
                  <dd>{{ qtyText(item.available_near_expiry_qty) }} {{ item.unit }}</dd>
                </div>
                <div>
                  <dt>{{ RECOMMENDED_LABEL }}</dt>
                  <dd>{{ qtyText(item.recommended_qty) }} {{ item.unit }}</dd>
                </div>
                <div v-if="confidenceLabel(item.confidence)">
                  <dt>{{ CONFIDENCE_FIELD_LABEL }}</dt>
                  <dd>{{ confidenceLabel(item.confidence) }}</dd>
                </div>
              </dl>
              <p v-if="item.min_batch_applied && item.reason" class="prep-min-batch">{{ item.reason }}</p>
              <template v-if="usableBatches(item).length">
                <h3 class="prep-batches-title">{{ BATCHES_LABEL }}</h3>
                <ul class="prep-batches">
                  <li v-for="batch in usableBatches(item)" :key="batch.batch_id">
                    {{ REMAINING_LABEL }} {{ qtyText(batch.remaining_qty) }} {{ item.unit }}
                    · {{ EXPIRES_AT_LABEL }} {{ formatPrepTime(batch.expires_at) }}
                  </li>
                </ul>
              </template>
            </details>
          </li>
        </ul>
      </section>
    </div>

    <details
      v-if="lowConfidence.length || missingRules.length"
      class="card prep-aux"
    >
      <summary>
        <template v-if="lowConfidence.length">{{ CONFIDENCE_LABELS.none }} {{ lowConfidence.length }} · </template>
        缺规则 {{ missingRules.length }}
      </summary>
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

    <ConfirmDialog
      v-if="discardTarget"
      class="prep-discard-confirm"
      :title="discardCopy.title"
      :message="discardCopy.body"
      :confirm-label="discardCopy.confirmLabel"
      danger
      @confirm="discardBatch(discardTarget)"
      @cancel="cancelDiscard"
    />
    <TextExportModal
      v-if="exportOpen"
      :content="exportText"
      :allow-push="false"
      :title="EXPORT_TITLE"
      :hint="EXPORT_HINT"
      @close="exportOpen = false"
    />
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
  gap: 8px;
}
.prep-station-chips {
  margin-top: 8px;
}
.prep-preset,
.prep-refresh,
.prep-export,
.prep-record,
.prep-extra,
.prep-undo,
.prep-discard {
  min-height: 44px;
  padding: 10px 18px;
  font-size: 15px;
}
.prep-row-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px;
}
.prep-preset.is-active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.prep-custom-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 10px 0 4px;
}
.prep-custom-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--text-dim);
}
.prep-datetime {
  min-height: 44px;
  min-width: 220px;
  font-size: 16px;
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
.prep-board,
.prep-expiring-list,
.prep-rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.prep-expiring-list,
.prep-rows {
  list-style: none;
  margin: 0;
  padding: 0;
  gap: 0;
}
.prep-station-title {
  font-size: 16px;
  font-weight: 700;
  margin: 0 0 8px;
}
.prep-expiring-row,
.prep-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 4px;
  border-top: 1px solid var(--border);
}
.prep-row {
  padding-left: 12px;
  border-left: 4px solid var(--border);
}
.prep-row.is-skip {
  border-left-color: var(--green);
}
.prep-row.is-todo {
  border-left-color: var(--yellow);
}
.prep-row.is-danger {
  border-left-color: var(--red);
}
.prep-expiring-row:first-child,
.prep-row:first-child {
  border-top: 0;
}
.prep-expiring-main {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.prep-item-name {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
}
.prep-sample-badge {
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
.prep-rec-qty.is-skip {
  color: var(--green);
}
.prep-rec-qty.is-todo {
  color: var(--yellow);
}
.prep-rec-qty.is-danger {
  color: var(--red);
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
.prep-no-master {
  margin: 0;
  font-size: 13px;
  color: var(--text-dim);
}
.prep-register {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px;
}
.prep-qty-field {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--text-dim);
}
.prep-qty-input {
  width: 96px;
  min-height: 44px;
  font-size: 16px;
}
.prep-aux {
  font-size: 13px;
  color: var(--text-dim);
}
.prep-aux-list {
  margin: 8px 0 0;
  padding-left: 18px;
}
.prep-row-more {
  font-size: 13px;
  color: var(--text-dim);
}
.prep-row-more summary {
  cursor: pointer;
  min-height: 32px;
  font-size: 13px;
  color: var(--text);
}
.prep-formula {
  display: grid;
  gap: 6px;
  margin: 8px 0 0;
}
.prep-formula > div {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.prep-formula dt {
  min-width: 7em;
  font-weight: 600;
  color: var(--text-dim);
}
.prep-formula dd {
  margin: 0;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.prep-min-batch {
  margin: 8px 0 0;
  color: var(--text);
}
.prep-batches-title {
  margin: 12px 0 6px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}
.prep-batches {
  margin: 0;
  padding-left: 18px;
  color: var(--text);
}
@media (max-width: 720px) {
  .prep-board,
  .prep-expiring-list,
  .prep-rows,
  .prep-expiring-row,
  .prep-row {
    display: flex;
    flex-direction: column;
    grid-template-columns: none;
  }
}
:deep(.prep-discard-confirm .btn) {
  min-height: 44px;
  padding: 10px 18px;
  font-size: 15px;
}
</style>
