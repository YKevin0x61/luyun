<script setup>
import { computed, onMounted } from 'vue'
import TextExportModal from '../components/salesreport/TextExportModal.vue'
import { usePrepPlan } from '../composables/usePrepPlan'
import { workbenchDocumentTitle } from '../utils/workbenchCopy'
import { usePrepPlanAdmin } from '../composables/usePrepPlanAdmin'
import { useStationsStore } from '../stores/stations'
import {
  ALL_STATIONS_LABEL,
  CONFIDENCE_LABELS,
  CONFIDENCE_FIELD_LABEL,
  CUSTOM_TIME_LABEL,
  CLOSED_DAY_ADD_LABEL,
  CLOSED_DAY_HINT,
  CLOSED_DAY_NOTE_PLACEHOLDER,
  CLOSED_DAY_REMOVE_LABEL,
  CLOSED_DAY_TITLE,
  CLOSED_DAYS_EMPTY,
  CLOSED_DAYS_HINT,
  CLOSED_DAYS_TITLE,
  EMPTY_HINT,
  EXPORT_HINT,
  EXPORT_LABEL,
  EXPORT_TITLE,
  FORECAST_LABEL,
  FORMULA_LABEL,
  PREP_PLAN_TITLE,
  PRESET_LABELS,
  READ_ONLY_HINT,
  RECOMMENDED_LABEL,
  REFRESH_LABEL,
  REFRESHING_LABEL,
  SAFETY_LABEL,
  SAFETY_TIER_HINT,
  TODO_COUNT_LABEL,
  UNCATEGORIZED_STATION,
  confidenceLabel,
  safetyRatioText,
} from '../utils/prepPlanCopy'
import { buildPrepPlanText } from '../utils/prepPlanText'
import {
  PRESET_AFTERNOON,
  PRESET_CUSTOM,
  PRESET_FUTURE_24H,
  PRESET_MORNING,
  formatPrepTime,
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
  busy,
  statusText,
  errorText,
  items,
  missingRules,
  lowConfidence,
  windowStart,
  windowEnd,
  closedReason,
  closedDays,
  newClosedDay,
  newClosedNote,
  closedError,
  exportOpen,
  windowRange,
  refresh,
  loadClosedDays,
  addClosedDay,
  removeClosedDay,
} = usePrepPlan()

// 票 08：这一页两种身份都能打开（工作台「后勤」组、清单里 `audience: both`）。写入口一颗
// 都不渲染 —— 备货数量不登记、每次计算都按 0 库存，读与写在这里已经不是同一个问题了。
// 身份判据（`composables/usePrepPlanAdmin.js`）留着只为一件事：那句话只说给员工听 ——
// 管理端在这一页本来就没有能改的东西，不必再解释一遍。
// 读路径（刷新建议 / 复制清单 / 时间窗 / 档口筛选）两档都在：它们不写库。
const { readOnly } = usePrepPlanAdmin()

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
onMounted(() => {
  // 这一页原来**一个 document.title 都没设**：真机走查里它的浏览器标签是 index.html 的
  // 默认值「厨务管家 · 管理后台」，跟管理后台任意一页分不开（手机上多标签时尤其明显）。
  document.title = workbenchDocumentTitle('备货计划')
  stationsStore.load()
  loadClosedDays()
})

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

const exportText = computed(() =>
  buildPrepPlanText({
    windowStart: windowStart.value,
    windowEnd: windowEnd.value,
    stations: board.value.map((group) => ({
      stationLabel: group.label,
      items: group.items,
    })),
    missingRules: missingRules.value,
  })
)

function qtyText(value) {
  return Number.isFinite(Number(value)) ? String(Math.round(Number(value))) : '0'
}

function isLowSample(item) {
  return item.confidence === 'low'
}
</script>

<template>
  <div class="prep-plan" :aria-busy="busy ? 'true' : 'false'">
    <div class="card prep-toolbar">
      <header class="prep-head">
        <h1 class="prep-title">{{ PREP_PLAN_TITLE }}</h1>
        <p v-if="kitchenItems.length" class="prep-todo">{{ TODO_COUNT_LABEL }} {{ todoCount }} 项</p>
      </header>
      <!-- 员工这一档：明说这一页只读（票 08）。这一页对谁都没有写控件，员工不会因为
           「少了几颗按钮」而犯嘀咕 —— 那句话是替这件事说的。 -->
      <p v-if="readOnly" class="prep-readonly">{{ READ_ONLY_HINT }}</p>
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

    <!-- 歇业日维护区：产品要求"在工作台里能改"，不是让人去数据管理页填表。
         写控件只给管理端（后端写端点也挂 verify_admin_token，两边一致）。 -->
    <details class="card prep-closed-days">
      <summary>{{ CLOSED_DAYS_TITLE }}</summary>
      <p class="prep-closed-hint">{{ CLOSED_DAYS_HINT }}</p>
      <ul v-if="closedDays.length" class="prep-closed-list">
        <li v-for="row in closedDays" :key="row.day">
          <span class="prep-closed-day">{{ row.day }}</span>
          <span v-if="row.note" class="prep-closed-note">{{ row.note }}</span>
          <button
            v-if="!readOnly"
            type="button"
            class="btn prep-closed-remove"
            @click="removeClosedDay(row.day)"
          >
            {{ CLOSED_DAY_REMOVE_LABEL }}
          </button>
        </li>
      </ul>
      <p v-else class="prep-closed-hint">{{ CLOSED_DAYS_EMPTY }}</p>
      <div v-if="!readOnly" class="prep-closed-add">
        <input v-model="newClosedDay" type="date" class="prep-closed-input" />
        <input
          v-model="newClosedNote"
          type="text"
          class="prep-closed-input"
          :placeholder="CLOSED_DAY_NOTE_PLACEHOLDER"
        />
        <button
          type="button"
          class="btn btn-primary"
          :disabled="!newClosedDay"
          @click="addClosedDay"
        >
          {{ CLOSED_DAY_ADD_LABEL }}
        </button>
      </div>
      <p v-if="closedError" class="prep-closed-error">{{ closedError }}</p>
    </details>

    <div v-if="!kitchenItems.length" class="card prep-empty">
      <p v-if="closedReason" class="empty-state">
        <strong>{{ CLOSED_DAY_TITLE }}</strong> —— {{ closedReason }} {{ CLOSED_DAY_HINT }}
      </p>
      <p v-else class="empty-state">{{ errorText ? errorText : EMPTY_HINT }}</p>
      <button
        type="button"
        class="btn btn-primary prep-refresh"
        :disabled="busy"
        @click="refresh"
      >
        {{ refreshButtonLabel }}
      </button>
    </div>

    <div v-else class="prep-board">
      <section v-for="group in board" :key="group.stationId" class="card prep-station">
        <h2 class="prep-station-title">{{ group.label }}</h2>
        <ul class="prep-rows">
          <li
            v-for="item in group.items"
            :key="`${item.item_name}|${item.unit}`"
            class="prep-row"
            :class="Number(item.recommended_qty || 0) <= 0 ? 'is-skip' : 'is-todo'"
          >
            <div class="prep-row-main">
              <div class="prep-item-name">
                {{ item.item_name }}
                <span v-if="isLowSample(item)" class="badge prep-sample-badge">{{ CONFIDENCE_LABELS.low }}</span>
              </div>
              <div class="prep-stamp" :class="Number(item.recommended_qty || 0) <= 0 ? 'is-skip' : 'is-todo'">
                <span class="prep-stamp-label">{{ RECOMMENDED_LABEL }}</span>
                <span class="prep-stamp-qty">{{ qtyText(item.recommended_qty) }}</span>
                <span class="prep-stamp-unit">{{ item.unit }}</span>
                <span v-if="Number(item.recommended_qty || 0) <= 0" class="prep-skip">不用做</span>
              </div>
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
                  <dd>
                    {{ qtyText(item.safety_qty) }} {{ item.unit }}
                    <span v-if="safetyRatioText(item.effective_safety_ratio)" class="prep-safety-ratio">
                      （{{ safetyRatioText(item.effective_safety_ratio) }}）
                    </span>
                  </dd>
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
              <!-- 裕度按品不同（ADR 0110）：同一个品比以前做得少时，师傅要能自己查到为什么。
                   复用 .prep-min-batch 的「面板里一句小字说明」样式，免得碰 CSS 契约测试。 -->
              <p class="prep-min-batch">{{ SAFETY_TIER_HINT }}</p>
              <p v-if="item.min_batch_applied && item.reason" class="prep-min-batch">{{ item.reason }}</p>
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
/* 员工这一档顶上那一句（票 08）：一眼看得出这一页改不了，不占太多地方。 */
.prep-readonly {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(10, 13, 22, 0.45);
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
.prep-export {
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
.prep-rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
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
.prep-row:first-child {
  border-top: 0;
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
  .prep-action-row .prep-export {
    width: auto;
    flex: 0 0 auto;
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
}
@media (prefers-reduced-motion: reduce) {
  .prep-plan .btn,
  .prep-plan .btn:active:not(:disabled) {
    transition: none;
    transform: none;
  }
}

/* 歇业日维护区：折叠着，日常不占视线，要改的时候点开 */
.prep-closed-days {
  margin-bottom: 12px;
  padding: 12px 16px;
}
.prep-closed-days > summary {
  cursor: pointer;
  font-weight: 600;
}
.prep-closed-hint,
.prep-closed-note {
  color: var(--prep-mute);
  font-size: 13px;
}
.prep-closed-list {
  list-style: none;
  margin: 8px 0;
  padding: 0;
}
.prep-closed-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}
.prep-closed-day {
  font-variant-numeric: tabular-nums;
}
.prep-closed-remove {
  margin-left: auto;
}
.prep-closed-add {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.prep-closed-input {
  flex: 1 1 140px;
  min-width: 0;
}
.prep-closed-error {
  color: var(--red);
  font-size: 13px;
  margin: 8px 0 0;
}
</style>
