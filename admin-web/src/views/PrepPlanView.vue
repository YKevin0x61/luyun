<script setup>
import { computed, onMounted } from 'vue'
import ConfirmDialog from '../components/admin/ConfirmDialog.vue'
import TextExportModal from '../components/salesreport/TextExportModal.vue'
import LuyunNumberInput from '../components/ui/LuyunNumberInput.vue'
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
  RECORD_QTY_LABEL,
  REFRESH_LABEL,
  REFRESHING_LABEL,
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
  windowRange,
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

const windowCaption = computed(() => {
  const { start, end } = windowRange()
  return `${formatPrepTime(start)} – ${formatPrepTime(end)}`
})

const refreshButtonLabel = computed(() =>
  busy.value && statusText.value === REFRESHING_LABEL ? REFRESHING_LABEL : REFRESH_LABEL
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
  <div class="prep-plan" :aria-busy="busy ? 'true' : 'false'">
    <div class="card prep-toolbar">
      <header class="prep-head">
        <h1 class="prep-title">{{ PREP_PLAN_TITLE }}</h1>
        <p v-if="kitchenItems.length" class="prep-todo">{{ TODO_COUNT_LABEL }} {{ todoCount }} 项</p>
      </header>
      <div class="prep-toolbar-row prep-preset-row" role="group" aria-label="时间窗">
        <button
          v-for="chip in PRESET_CHIPS"
          :key="chip.id"
          type="button"
          class="btn prep-chip"
          :class="{ 'is-active': preset === chip.id }"
          :aria-pressed="preset === chip.id"
          @click="selectPreset(chip.id)"
        >
          {{ chip.label }}
        </button>
        <button
          type="button"
          class="btn prep-chip"
          :class="{ 'is-active': preset === PRESET_CUSTOM }"
          :aria-pressed="preset === PRESET_CUSTOM"
          :aria-expanded="showCustom"
          @click="toggleCustom"
        >
          {{ CUSTOM_TIME_LABEL }}
        </button>
      </div>
      <p class="prep-window-caption">{{ windowCaption }}</p>
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
      <div class="prep-toolbar-row prep-action-row">
        <button
          type="button"
          class="btn btn-primary prep-refresh"
          :disabled="busy"
          :aria-busy="busy && statusText === REFRESHING_LABEL ? 'true' : 'false'"
          @click="refresh"
        >
          {{ refreshButtonLabel }}
        </button>
        <button
          type="button"
          class="btn prep-export"
          @click="exportOpen = true"
        >
          {{ EXPORT_LABEL }}
        </button>
      </div>
      <p class="prep-status" :class="{ 'is-error': errorText }" role="status" aria-live="polite">
        {{ errorText || statusText }}
      </p>
      <div class="prep-toolbar-row prep-station-chips" role="group" aria-label="档口">
        <button
          v-for="chip in stationChips"
          :key="chip.id || 'all'"
          type="button"
          class="btn prep-chip"
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
            <div class="prep-metrics prep-metrics--expiring">
              <div class="prep-metric">
                <span class="prep-metric-label">{{ REMAINING_LABEL }}</span>
                <span class="prep-metric-value">{{ qtyText(batch.remaining_qty) }}</span>
                <span class="prep-metric-unit">{{ batch.unit }}</span>
              </div>
              <div class="prep-metric prep-metric--wide">
                <span class="prep-metric-label">{{ EXPIRES_AT_LABEL }}</span>
                <span class="prep-metric-value prep-metric-time">{{ formatPrepTime(batch.expires_at) }}</span>
              </div>
            </div>
          </div>
          <button
            type="button"
            class="btn btn-danger prep-discard"
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
      <button
        type="button"
        class="btn btn-primary prep-refresh"
        :disabled="busy"
        @click="refresh"
      >
        {{ refreshButtonLabel }}
      </button>
    </div>

    <div v-else-if="kitchenItems.length" class="prep-board">
      <section v-for="group in board" :key="group.stationId" class="card prep-station">
        <h2 class="prep-station-title">{{ group.label }}</h2>
        <ul class="prep-rows">
          <li v-for="item in group.items" :key="`${item.item_name}|${item.unit}`" class="prep-row" :class="`is-${rowTone(item)}`">
            <div class="prep-row-main">
              <div class="prep-item-name">
                {{ item.item_name }}
                <span v-if="isLowSample(item)" class="badge prep-sample-badge">{{ CONFIDENCE_LABELS.low }}</span>
              </div>
              <div class="prep-stamp" :class="`is-${rowTone(item)}`">
                <span class="prep-stamp-label">{{ RECOMMENDED_LABEL }}</span>
                <span class="prep-stamp-qty">{{ qtyText(item.recommended_qty) }}</span>
                <span class="prep-stamp-unit">{{ item.unit }}</span>
                <span v-if="Number(item.recommended_qty || 0) <= 0" class="prep-skip">{{ SKIP_LABEL }}</span>
              </div>
            </div>
            <div class="prep-metrics">
              <div class="prep-metric">
                <span class="prep-metric-label">{{ FRESH_AVAILABLE_LABEL }}</span>
                <span class="prep-metric-value">{{ qtyText(item.available_fresh_qty) }}</span>
                <span class="prep-metric-unit">{{ item.unit }}</span>
              </div>
              <div class="prep-metric" :class="{ 'is-warn': Number(item.available_near_expiry_qty || 0) > 0 }">
                <span class="prep-metric-label">{{ NEAR_AVAILABLE_LABEL }}</span>
                <span class="prep-metric-value">{{ qtyText(item.available_near_expiry_qty) }}</span>
                <span class="prep-metric-unit">{{ item.unit }}</span>
              </div>
              <div class="prep-metric">
                <span class="prep-metric-label">{{ PRODUCED_LABEL }}</span>
                <span class="prep-metric-value">{{ qtyText(item.produced_qty) }}</span>
                <span class="prep-metric-unit">{{ item.unit }}</span>
              </div>
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
                <div class="prep-qty-field">
                  <span class="prep-qty-label">{{ RECORD_QTY_LABEL }}</span>
                  <LuyunNumberInput
                    v-model="registerQty[itemKey(item)]"
                    decimal
                    :step="1"
                    :disabled="busy"
                  />
                  <span class="prep-qty-unit">{{ item.unit }}</span>
                </div>
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
      class="prep-export-modal"
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
  --prep-mute: #9ca3af;
  --prep-chit: #161c2e;
  --prep-stamp-todo: rgba(245, 158, 11, 0.16);
  --prep-stamp-skip: rgba(34, 197, 94, 0.12);
  --prep-stamp-danger: rgba(239, 68, 68, 0.16);
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-x: clip;
  max-width: 960px;
}
.prep-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 14px;
  margin-bottom: 10px;
}
.prep-title {
  font-size: 20px;
  font-weight: 700;
  margin: 0;
}
.prep-todo {
  margin: 0;
  font-size: 14px;
  color: var(--prep-mute);
}
.prep-toolbar-row {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 8px;
}
.prep-preset-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.prep-station-chips {
  margin-top: 8px;
}
.prep-chip,
.prep-refresh,
.prep-export,
.prep-record,
.prep-extra,
.prep-undo,
.prep-discard {
  min-height: 44px;
  padding: 10px 16px;
  font-size: 15px;
  touch-action: manipulation;
  white-space: nowrap;
}
.prep-chip.is-active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.prep-action-row .prep-refresh,
.prep-action-row .prep-export {
  flex: 1 1 140px;
  justify-content: center;
}
.prep-window-caption {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--prep-mute);
  font-variant-numeric: tabular-nums;
}
.prep-custom-fields {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  margin: 10px 0 4px;
}
.prep-custom-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--prep-mute);
  min-width: 0;
}
.prep-datetime {
  min-height: 44px;
  width: 100%;
  min-width: 0;
  font-size: 16px;
}
.prep-status {
  margin: 8px 0 0;
  min-height: 1.5em;
  font-size: 13px;
  line-height: 1.5;
  color: var(--prep-mute);
}
.prep-status.is-error {
  color: var(--red);
}
.prep-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  text-align: center;
}
.prep-empty .empty-state {
  padding: 16px 12px 0;
  font-size: 15px;
  line-height: 1.5;
}
.prep-empty .prep-refresh {
  width: 100%;
  max-width: 280px;
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
  gap: 10px;
  padding: 14px 0;
  border-top: 1px solid var(--border);
}
.prep-row {
  padding: 14px 12px 14px 14px;
  margin: 0 -4px;
  border-left: 4px solid var(--border);
  background: var(--prep-chit);
  border-radius: 0 10px 10px 0;
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
.prep-expiring-row {
  gap: 12px;
}
.prep-expiring-main {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.prep-item-name {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.prep-sample-badge {
  font-weight: 600;
}
.prep-row-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 10px 12px;
}
.prep-stamp {
  display: grid;
  justify-items: end;
  gap: 2px;
  min-width: 88px;
  padding: 8px 10px;
  border-radius: 10px;
  background: var(--prep-stamp-todo);
  font-variant-numeric: tabular-nums;
}
.prep-stamp.is-skip {
  background: var(--prep-stamp-skip);
}
.prep-stamp.is-danger {
  background: var(--prep-stamp-danger);
}
.prep-stamp-label {
  font-size: 11px;
  letter-spacing: 0.02em;
  color: var(--prep-mute);
}
.prep-stamp-qty {
  font-size: 32px;
  font-weight: 700;
  line-height: 1;
  color: var(--yellow);
}
.prep-stamp.is-skip .prep-stamp-qty {
  color: var(--green);
}
.prep-stamp.is-danger .prep-stamp-qty {
  color: var(--red);
}
.prep-stamp-unit,
.prep-skip {
  font-size: 13px;
  font-weight: 700;
}
.prep-stamp-unit {
  color: var(--prep-mute);
}
.prep-skip {
  color: var(--green);
}
.prep-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.prep-metrics--expiring {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
}
.prep-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 8px;
  border-radius: 8px;
  background: rgba(10, 13, 22, 0.45);
}
.prep-metric.is-warn {
  background: rgba(245, 158, 11, 0.12);
}
.prep-metric-label {
  font-size: 11px;
  line-height: 1.4;
  color: var(--prep-mute);
}
.prep-metric-value {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--text);
  overflow-wrap: anywhere;
}
.prep-metric-time {
  font-size: 14px;
  font-weight: 600;
}
.prep-metric-unit {
  font-size: 12px;
  color: var(--prep-mute);
}
.prep-no-master {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--prep-mute);
}
.prep-row-actions {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}
.prep-register {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.prep-qty-field {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--prep-mute);
}
.prep-qty-label,
.prep-qty-unit {
  white-space: nowrap;
}
.prep-register :deep(.luyun-number) {
  width: 100%;
}
.prep-register :deep(.luyun-number__step) {
  width: 44px;
  min-height: 44px;
}
.prep-register :deep(.luyun-number__input) {
  min-height: 44px;
  font-size: 16px;
}
.prep-record,
.prep-extra,
.prep-undo,
.prep-discard {
  width: 100%;
  justify-content: center;
}
.prep-aux {
  font-size: 13px;
  color: var(--prep-mute);
}
.prep-aux summary,
.prep-row-more summary {
  cursor: pointer;
  min-height: 44px;
  display: flex;
  align-items: center;
  font-size: 14px;
  color: var(--text);
  touch-action: manipulation;
}
.prep-aux-list {
  margin: 8px 0 0;
  padding-left: 18px;
  line-height: 1.5;
}
.prep-row-more {
  font-size: 13px;
  color: var(--prep-mute);
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
  color: var(--prep-mute);
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
.prep-plan :is(button, .btn, input, summary):focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.prep-plan .btn:active:not(:disabled) {
  transform: scale(0.98);
}
.prep-plan :deep(.modal-overlay) {
  padding: 12px;
  padding-bottom: max(12px, env(safe-area-inset-bottom, 0px));
  align-items: flex-end;
}
.prep-plan :deep(.modal-box) {
  width: min(600px, 100%) !important;
  max-height: min(86vh, 86dvh);
}
.prep-plan :deep(textarea.input) {
  min-height: 180px !important;
}
.prep-plan :deep(.modal-footer) {
  flex-wrap: wrap;
}
.prep-plan :deep(.modal-footer .btn),
.prep-plan :deep(.modal-header .btn) {
  min-height: 44px;
  min-width: 44px;
  flex: 1 1 120px;
  justify-content: center;
  font-size: 15px;
}
.prep-plan :deep(.modal-header .btn) {
  flex: 0 0 44px;
  padding: 0;
}
@media (min-width: 721px) {
  .prep-preset-row {
    display: flex;
    grid-template-columns: none;
  }
  .prep-custom-fields {
    grid-template-columns: 1fr 1fr;
  }
  .prep-action-row .prep-refresh,
  .prep-action-row .prep-export,
  .prep-record,
  .prep-extra,
  .prep-undo,
  .prep-discard {
    width: auto;
    flex: 0 0 auto;
  }
  .prep-row-actions {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: flex-end;
  }
  .prep-register {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: flex-end;
  }
  .prep-qty-field {
    min-width: 220px;
  }
  .prep-plan :deep(.modal-overlay) {
    align-items: center;
  }
}
@media (max-width: 720px) {
  .prep-title {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  .prep-head {
    margin-bottom: 8px;
  }
  .prep-todo {
    font-size: 16px;
    font-weight: 700;
    color: var(--text);
  }
  .prep-chip {
    justify-content: center;
    min-width: 0;
  }
  .prep-metrics--expiring {
    grid-template-columns: 1fr;
  }
}
@media (prefers-reduced-motion: reduce) {
  .prep-plan .btn,
  .prep-plan .btn:active:not(:disabled) {
    transition: none;
    transform: none;
  }
}
</style>
