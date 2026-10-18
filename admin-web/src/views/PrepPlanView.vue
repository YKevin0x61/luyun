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
    <!-- 页头：H1 一行 + 右侧主动作（v3：手机上 H1 与筛选/动作分两行） -->
    <header class="prep-page-head">
      <div class="prep-head-copy">
        <p class="prep-kicker">备货计划 · 备货建议</p>
        <h1 class="prep-title">{{ PREP_PLAN_TITLE }}</h1>
      </div>
      <div class="prep-head-side">
        <button
          type="button"
          class="btn btn-primary prep-refresh"
          :disabled="busy"
          :aria-busy="busy && statusText === REFRESHING_LABEL ? 'true' : 'false'"
          @click="refresh"
        >
          {{ refreshButtonLabel }}
        </button>
      </div>
    </header>

    <!-- 员工这一档：明说这一页只读（票 08）。这一页对谁都没有写控件，员工不会因为
         「少了几颗按钮」而犯嘀咕 —— 那句话是替这件事说的。 -->
    <p v-if="readOnly" class="prep-readonly">{{ READ_ONLY_HINT }}</p>

    <!-- 44px 工具条：左时间窗分段 + 站点 chips 横滚，右更新时间。
         时间窗不再是面板（v3 审查：一块面板 + 面板头白占 200px / 手机 270px）。 -->
    <div class="prep-toolbar">
      <div class="prep-seg" role="group" aria-label="时间窗">
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
      <span class="prep-toolbar-sep" aria-hidden="true"></span>
      <div class="prep-station-chips" role="group" aria-label="档口">
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
      <p class="prep-status" :class="{ 'is-error': errorText }" role="status" aria-live="polite">
        {{ errorText || statusText }}
      </p>
    </div>
    <div v-if="showCustom" class="prep-toolbar-row prep-custom-fields">
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

    <!-- 状态条：歇业日 / 样本不足 / 缺规则 三个单读数面板并成一条（v3 审查）。 -->
    <div class="prep-statusbar">
      <details class="prep-aux prep-stat">
        <summary class="prep-stat-chip">
          <i class="prep-led warn" aria-hidden="true"></i>歇业 <b>{{ closedDays.length }}</b> 天
        </summary>
        <div class="prep-stat-body">
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
        </div>
      </details>
      <details
        v-if="lowConfidence.length || missingRules.length"
        class="prep-aux prep-stat"
      >
        <summary class="prep-stat-chip">
          <i class="prep-led bad" aria-hidden="true"></i>样本不足 <b>{{ lowConfidence.length }}</b> · 缺规则 <b>{{ missingRules.length }}</b>
        </summary>
        <div class="prep-stat-body">
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
        </div>
      </details>
    </div>

    <!-- 主区：左列表（3fr） / 右 sticky 汇总（1fr） -->
    <div class="prep-body">
      <section class="prep-panel prep-main">
        <header class="prep-panel-head">
          <i class="prep-led sig" aria-hidden="true"></i>
          <h2 class="prep-panel-title">备货建议</h2>
          <span class="prep-hint">按档口 · {{ filteredKitchenItems.length }} 项</span>
        </header>

        <!-- 空态：横条（一行文字 + 一个动作），不占面板头 -->
        <div v-if="!kitchenItems.length" class="prep-empty-strip">
          <p v-if="closedReason" class="prep-empty-text">
            <strong>{{ CLOSED_DAY_TITLE }}</strong> —— {{ closedReason }} {{ CLOSED_DAY_HINT }}
          </p>
          <p v-else class="prep-empty-text">{{ errorText ? errorText : EMPTY_HINT }}</p>
          <button
            type="button"
            class="btn btn-primary prep-refresh"
            :disabled="busy"
            @click="refresh"
          >
            {{ refreshButtonLabel }}
          </button>
        </div>

        <!-- 档口分段：一个主面板内按档口分段（v3：一屏不超过 3 个面板头） -->
        <div v-else class="prep-station-segs">
          <section v-for="group in board" :key="group.stationId" class="prep-seg-block">
            <header class="prep-seg-head">
              <i class="prep-led sig" aria-hidden="true"></i>
              <h3 class="prep-seg-title">{{ group.label }}</h3>
              <span class="prep-seg-count">{{ group.items.length }} 项</span>
            </header>
            <ul class="prep-rows">
              <li
                v-for="item in group.items"
                :key="`${item.item_name}|${item.unit}`"
                class="prep-row"
                :class="Number(item.recommended_qty || 0) <= 0 ? 'is-skip' : 'is-todo'"
              >
                <!-- 名称 + 建议量收在同一个视觉组（≤360px），不横跨整行 -->
                <div class="prep-row-main">
                  <span class="prep-item-name">
                    {{ item.item_name }}
                    <span v-if="isLowSample(item)" class="badge prep-sample-badge">{{ CONFIDENCE_LABELS.low }}</span>
                  </span>
                  <span class="prep-stamp" :class="Number(item.recommended_qty || 0) <= 0 ? 'is-skip' : 'is-todo'">
                    <span class="prep-stamp-label">{{ RECOMMENDED_LABEL }}</span>
                    <b class="prep-stamp-qty">{{ qtyText(item.recommended_qty) }}</b>
                    <span class="prep-stamp-unit">{{ item.unit }}</span>
                    <span v-if="Number(item.recommended_qty || 0) <= 0" class="prep-skip">不用做</span>
                  </span>
                </div>
                <!-- 「怎么算的」是行尾按钮；展开时内容占整行（不再整行留一条长白） -->
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
                  <!-- 裕度按品不同（ADR 0110）：同一个品比以前做得少时，师傅要能自己查到为什么。 -->
                  <p class="prep-min-batch">{{ SAFETY_TIER_HINT }}</p>
                  <p v-if="item.min_batch_applied && item.reason" class="prep-min-batch">{{ item.reason }}</p>
                </details>
              </li>
            </ul>
          </section>
        </div>
      </section>

      <!-- sticky 汇总：四个读数 + 复制清单（吃掉宽屏右侧空档） -->
      <aside class="prep-side">
        <section class="prep-panel prep-summary">
          <header class="prep-panel-head">
            <i class="prep-led warn" aria-hidden="true"></i>
            <h2 class="prep-panel-title">汇总</h2>
            <span class="prep-hint">当前时间窗</span>
          </header>
          <dl class="prep-readouts">
            <div class="prep-readout">
              <dt>{{ TODO_COUNT_LABEL }}</dt>
              <dd><b>{{ todoCount }}</b> 项</dd>
            </div>
            <div class="prep-readout">
              <dt>歇业</dt>
              <dd><b>{{ closedDays.length }}</b> 天</dd>
            </div>
            <div class="prep-readout">
              <dt>样本不足</dt>
              <dd><b>{{ lowConfidence.length }}</b></dd>
            </div>
            <div class="prep-readout">
              <dt>缺规则</dt>
              <dd><b>{{ missingRules.length }}</b></dd>
            </div>
          </dl>
          <p class="prep-summary-window">{{ windowCaption }}</p>
          <button
            type="button"
            class="btn btn-primary prep-export"
            @click="exportOpen = true"
          >
            {{ EXPORT_LABEL }}
          </button>
        </section>
      </aside>
    </div>

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
/* B 案 v2 + v3 布局纪律：页头（H1 + 主动作）→ 44px 工具条 → 状态条 → 3fr/1fr 主区。
   全部走语义令牌与等宽数字；浅 / 深两套主题各自生效，这里不写裸色值。 */
.prep-plan {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-x: clip;
  padding: 0 32px calc(40px + var(--safe-b));
  font-family: var(--font-sans);
  color: var(--ink);
}

/* ── 页头 ─────────────────────────────────────────────────────────────── */
.prep-page-head {
  display: flex;
  align-items: flex-end;
  gap: 16px;
}
.prep-head-copy {
  min-width: 0;
}
.prep-kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
.prep-title {
  margin: 0;
  font: 600 26px/1.05 var(--font-sans);
  letter-spacing: .01em;
  color: var(--ink);
}
.prep-head-side {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ── 44px 工具条：时间窗分段 + 站点 chips 横滚 + 更新时间 ─────────────── */
.prep-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 2px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.prep-seg {
  display: flex;
  gap: 6px;
  flex: 0 0 auto;
}
.prep-toolbar-sep {
  width: 1px;
  align-self: stretch;
  margin: 8px 2px;
  background: var(--line);
  flex: 0 0 1px;
}
.prep-station-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  padding-bottom: 2px;
}
.prep-station-chips .prep-chip {
  flex: 0 0 auto;
}
.prep-status {
  margin: 0;
  flex: 0 0 auto;
  max-width: 15rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font: 500 9.5px/1.4 var(--font-mono);
  color: var(--ink-faint);
}
.prep-status.is-error {
  color: var(--danger-bright);
}

/* ── 控件：44px 触控 / 16px 输入（iOS 不缩放）───────────────────────── */
.prep-toolbar-row {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 8px;
}
.prep-chip,
.prep-refresh,
.prep-export {
  min-height: 44px;
  padding: 10px 16px;
  font-size: 14px;
  touch-action: manipulation;
  white-space: nowrap;
}
.prep-seg .prep-chip,
.prep-station-chips .prep-chip {
  padding: 6px 12px;
  font-size: 12px;
}
.prep-chip.is-active {
  background: var(--action);
  border-color: var(--action);
  color: var(--action-ink);
}
.prep-custom-fields {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}
.prep-custom-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  font-size: 13px;
  color: var(--ink-dim);
}
.prep-datetime {
  min-height: 44px;
  width: 100%;
  min-width: 0;
  font-size: 16px;
}

/* 员工这一档顶上那一句（票 08） */
.prep-readonly {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
  font-size: 13px;
  line-height: 1.5;
  color: var(--ink-dim);
}

/* ── 状态条：歇业 / 样本不足 / 缺规则（v3：三条并成一条）─────────────── */
.prep-statusbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 8px;
}
.prep-stat {
  min-width: 0;
}
.prep-stat[open] {
  flex: 1 1 100%;
}
.prep-stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-1);
  font: 500 11px/1 var(--font-sans);
  color: var(--ink-dim);
  cursor: pointer;
  list-style: none;
  touch-action: manipulation;
}
.prep-stat-chip b {
  font: 500 13px/1 var(--font-mono);
  color: var(--ink);
}
.prep-stat-chip::-webkit-details-marker {
  display: none;
}
.prep-stat[open] .prep-stat-chip {
  border-color: var(--line-strong);
  color: var(--ink);
}
.prep-stat-body {
  margin-top: 6px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-1);
}
.prep-stat-body .prep-aux-list {
  margin: 0 0 6px;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--ink-dim);
}
.prep-stat-body .prep-aux-list:last-child {
  margin-bottom: 0;
}
.prep-closed-hint,
.prep-closed-note {
  margin: 0 0 6px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--ink-faint);
}
.prep-closed-list {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
}
.prep-closed-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid var(--wb-ink-a08);
}
.prep-closed-list li:last-child {
  border-bottom: 0;
}
.prep-closed-day {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.prep-closed-remove {
  margin-left: auto;
}
.prep-closed-add {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.prep-closed-input {
  flex: 1 1 140px;
  min-width: 0;
}
.prep-closed-error {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--danger-bright);
}

/* ── 主区：3fr / 1fr ─────────────────────────────────────────────────── */
.prep-body {
  display: grid;
  grid-template-columns: 3fr 1fr;
  gap: 14px;
  align-items: start;
}
.prep-panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  overflow: hidden;
}
.prep-main {
  min-width: 0;
}
.prep-panel-head {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 44px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--line);
}
.prep-panel-title {
  margin: 0;
  font: 600 12.5px/1 var(--font-sans);
  letter-spacing: .06em;
  color: var(--ink);
}
.prep-hint {
  margin-left: auto;
  font: 500 9.5px/1 var(--font-mono);
  color: var(--ink-faint);
}
.prep-led {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex: 0 0 6px;
}
.prep-led.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.prep-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.prep-led.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.prep-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }

/* 空态：横条，不占面板头 */
.prep-empty-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 14px;
  min-height: 56px;
  padding: 10px 14px;
}
.prep-empty-text {
  margin: 0;
  flex: 1 1 12rem;
  font-size: 13px;
  line-height: 1.5;
  color: var(--ink-dim);
}

/* 档口分段（一个主面板内用 border-top 分段，不再每个档口一块面板） */
.prep-seg-block {
  border-top: 1px solid var(--line);
}
.prep-seg-block:first-child {
  border-top: 0;
}
.prep-seg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px 2px;
}
.prep-seg-title {
  margin: 0;
  font: 600 12px/1 var(--font-sans);
  letter-spacing: .06em;
  color: var(--ink);
}
.prep-seg-count {
  margin-left: auto;
  font: 500 9.5px/1 var(--font-mono);
  color: var(--ink-faint);
}

/* 行：56px 高；LED | 名称+建议量（同一视觉组 ≤360px）| 行尾「怎么算的」 */
.prep-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.prep-row {
  display: grid;
  grid-template-columns: 6px minmax(0, 1fr) 7rem;
  column-gap: 10px;
  align-items: center;
  min-height: 56px;
  padding: 6px 14px;
  border-top: 1px solid var(--wb-ink-a08);
  transition: background .15s var(--ease);
}
.prep-row:hover {
  background: var(--surface-2);
}
.prep-row::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warn);
}
.prep-row.is-skip::before { background: var(--ok); }
.prep-row.is-todo::before { background: var(--warn); }
.prep-row-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: baseline;
  gap: 8px 12px;
  max-width: 360px;
}
.prep-item-name {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.prep-sample-badge {
  font-weight: 600;
}
/* 建议量：一句「小字标签 + mono 数字 + 单位」，状态色只给数字。 */
.prep-stamp {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  justify-self: start;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.prep-stamp-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font: 500 9px/1 var(--font-sans);
  letter-spacing: .12em;
  color: var(--ink-faint);
}
.prep-stamp-qty {
  font: 500 16px/1 var(--font-mono);
  letter-spacing: -.02em;
  color: var(--warn);
}
.prep-stamp.is-skip .prep-stamp-qty {
  color: var(--ok);
}
.prep-stamp-unit {
  font-size: 10.5px;
  color: var(--ink-faint);
}
.prep-skip {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--ok);
}

/* 「怎么算的」：行尾小按钮；展开时占整行 */
.prep-row-more {
  grid-column: 3;
  justify-self: end;
  min-width: 0;
}
.prep-row-more[open] {
  grid-column: 2 / -1;
  justify-self: stretch;
}
.prep-aux summary,
.prep-row-more summary {
  cursor: pointer;
  min-height: 44px;
  display: flex;
  align-items: center;
  font-size: 11px;
  color: var(--ink-dim);
  touch-action: manipulation;
  list-style: none;
}
.prep-row-more summary {
  gap: 5px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
  white-space: nowrap;
}
.prep-aux summary {
  color: var(--ink-dim);
}
.prep-row-more summary::-webkit-details-marker,
.prep-aux summary::-webkit-details-marker {
  display: none;
}
.prep-row-more summary::before {
  content: "▸";
  font-size: 9px;
  color: var(--ink-faint);
}
.prep-row-more[open] summary::before {
  content: "▾";
}
.prep-row-more[open] summary {
  width: fit-content;
  margin-left: auto;
}
.prep-formula {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 11rem), 1fr));
  gap: 6px 14px;
  margin: 0 0 8px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
  font-size: 12px;
}
.prep-formula > div {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.prep-formula dt {
  min-width: 6.5em;
  font-weight: 500;
  color: var(--ink-faint);
}
.prep-formula dd {
  margin: 0;
  color: var(--ink);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.prep-min-batch {
  margin: 0 0 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-dim);
}

/* ── sticky 汇总（右栏）─────────────────────────────────────────────── */
.prep-side {
  position: sticky;
  top: 12px;
  min-width: 0;
}
.prep-readouts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 0;
  padding: 12px 14px;
}
.prep-readout {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.prep-readout dt {
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .16em;
  color: var(--ink-faint);
}
.prep-readout dd {
  margin: 0;
  font: 500 20px/1 var(--font-mono);
  color: var(--ink);
}
.prep-readout dd b {
  font: inherit;
  font-weight: 500;
}
.prep-summary-window {
  margin: 0;
  padding: 0 14px 10px;
  font: 500 9.5px/1.5 var(--font-mono);
  color: var(--ink-faint);
  overflow-wrap: anywhere;
}
.prep-summary .prep-export {
  display: flex;
  width: calc(100% - 28px);
  margin: 0 14px 14px;
  justify-content: center;
}

/* ── 焦点 / 动效 ─────────────────────────────────────────────────────── */
.prep-plan :is(button, .btn, input, summary):focus-visible {
  outline: 2px solid var(--focus-ring);
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

/* 工具条动作（保留契约：桌面不拉满） */
.prep-action-row .prep-refresh,
.prep-action-row .prep-export {
  flex: 1 1 140px;
  justify-content: center;
}

@media (min-width: 721px) {
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
@media (max-width: 900px) {
  .prep-body {
    grid-template-columns: 1fr;
  }
  .prep-side {
    order: -1;
    position: static;
  }
  .prep-readouts {
    grid-template-columns: repeat(4, 1fr);
  }
  .prep-summary .prep-export {
    width: auto;
    margin: 0 14px 12px;
    justify-content: center;
  }
}
@media (max-width: 720px) {
  .prep-plan {
    padding: 0 16px calc(40px + var(--safe-b));
  }
  /* v3：手机上 H1 一行、筛选/动作另起一行；标题不再视觉隐藏 */
  .prep-page-head {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }
  .prep-head-side {
    margin-left: 0;
  }
  .prep-toolbar {
    flex-wrap: wrap;
    padding: 6px 0;
  }
  .prep-toolbar-sep {
    display: none;
  }
  .prep-station-chips {
    flex: 1 1 100%;
    order: 3;
  }
  .prep-status {
    margin-left: auto;
  }
  .prep-chip {
    justify-content: center;
    min-width: 0;
  }
  .prep-row {
    grid-template-columns: 6px minmax(0, 1fr) auto;
  }
  .prep-row-main {
    max-width: none;
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
