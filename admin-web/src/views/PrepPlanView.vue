<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import MiniBarChart from '../components/prep/MiniBarChart.vue'
import { api } from '../api/client'
import { usePrepPlan } from '../composables/usePrepPlan'
import { useStationsStore } from '../stores/stations'
import { BASIS_HINT, COPIED_LABEL, COPY_LABEL, PREP_PLAN_TITLE } from '../utils/prepPlanCopy'
import { buildPrepPlanText } from '../utils/prepPlanText'
import { workbenchDocumentTitle } from '../utils/workbenchCopy'

/**
 * 备货计划（ADR 0117 重建）。
 *
 * 这一页只回答一个问题：**今天从现在到打烊，这个档口还要做多少、还要买多少。**
 * 没有时间窗选择器，没有「先点刷新」的空态，没有信心 / 裕度 / 风险读数。
 */

const stations = useStationsStore()
const {
  state,
  errorText,
  windowEnd,
  closed,
  isStaff,
  needsStationPick,
  staffStation,
  allStations,
  activeStation,
  expandedId,
  busyEntryId,
  stationTabs,
  groups,
  remainingCount,
  doneCount,
  doneInStation,
  load,
  toggleDone,
  setQty,
  pickStation,
  setKind,
  requestStationChange,
  toggleExpand,
} = usePrepPlan()

const editingId = ref(null)
const editingValue = ref('')
const copied = ref(false)

const staffStationLabel = computed(
  () => allStations.value.find((row) => row.id === staffStation.value)?.label || staffStation.value,
)

const title = PREP_PLAN_TITLE
workbenchDocumentTitle(title)

const windowText = computed(() => {
  if (closed.value) return '今天已打烊'
  if (!windowEnd.value) return ''
  const clock = windowEnd.value.slice(11, 16)
  return `现在 → ${clock}`
})

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

/** 用 UTC 构造再取星期，避免手机时区把日期挪到前一天。 */
function weekdayOf(day) {
  const parts = String(day || '').split('-').map(Number)
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return ''
  return WEEKDAYS[new Date(Date.UTC(parts[0], parts[1] - 1, parts[2])).getUTCDay()]
}

/** 近 7 天用量：后端给的是「今天往前」的顺序，画图要从早到晚。 */
function barPoints(entry) {
  const history = Array.isArray(entry.history7) ? entry.history7 : []
  return history
    .map((row) => ({
      value: Number(row.qty || 0),
      label: String(row.day || '').slice(5),
      weekday: weekdayOf(row.day),
    }))
    .reverse()
}

/** 保留 1 位小数（整数照常显示成整数）。师傅报的是约数，但 0.5 斤这类不能抹成 0。 */
function roundQty(value) {
  return Math.round((Number(value) || 0) * 10) / 10
}

function qtyText(entry) {
  return `${roundQty(entry.final_qty ?? entry.suggested_qty)}${entry.unit || ''}`
}

function isManual(entry) {
  return entry.qty_source === 'manual'
}

function startEdit(entry) {
  editingId.value = entry.id
  editingValue.value = String(Number(entry.final_qty ?? entry.suggested_qty ?? 0))
}

async function commitEdit(entry) {
  if (editingId.value !== entry.id) return
  const next = editingValue.value
  editingId.value = null
  if (String(entry.final_qty) === String(next) || next === '') return
  await setQty(entry, next)
}

function cancelEdit() {
  editingId.value = null
}

/** 展开后的公式说明：p40 → 整店水位 → 扣在库，三段都写出来。 */
function basisFormula(entry) {
  const basis = entry.basis || {}
  const days = (basis.weekdays || []).length
  const pct = Math.round(Number(basis.quantile || 0.4) * 100)
  const factor = Number(basis.store_factor || 1)
  const parts = [`这 ${days} 天同样钟点里偏保守的那档（p${pct}）`]
  if (Math.abs(factor - 1) >= 0.05) parts.push(`× 整店水位 ${factor.toFixed(2)}`)
  return parts.join(' ')
}

function basisText(entry) {
  const basis = entry.basis || {}
  if (basis.status === 'missing_rule') return '没有配方系数，算不出建议量'
  if (basis.status === 'no_history') return '近 8 周没有可比的同星期几'
  const rows = (basis.weekdays || []).map((row) => `${row.day.slice(5)} ${row.qty}`)
  const base = rows.length ? `近 ${rows.length} 个同星期几：${rows.join(' / ')}` : ''
  const extras = []
  const factor = Number(basis.store_factor || 1)
  if (Math.abs(factor - 1) >= 0.05) extras.push(`整店水位 ×${factor}`)
  return [base, ...extras].filter(Boolean).join('；')
}

async function copyList() {
  const text = buildPrepPlanText({
    windowEnd: windowEnd.value,
    stations: stationTabs.value.map((tab) => ({
      stationLabel: tab.label,
      items: groups.value
        .filter(() => tab.id === activeStation.value)
        .flatMap((group) => group.entries),
    })),
    closed: closed.value,
  })
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    window.setTimeout(() => (copied.value = false), 1600)
  } catch {
    copied.value = false
  }
}

onMounted(async () => {
  await load()
})

watch(activeStation, () => {
  void load()
})
</script>

<template>
  <div class="pp">
    <header class="pp-head">
      <div class="pp-head-main">
        <p class="pp-kicker">备货计划 · 参考值</p>
        <h1 class="pp-title">今天参考值 <b>{{ remainingCount }}</b> 项</h1>
        <p class="pp-sub">
          {{ windowText }}
          <span v-if="doneCount"> · 已做 {{ doneCount }} 项</span>
        </p>
      </div>
      <div class="pp-head-side">
        <button type="button" class="pp-copy" @click="copyList">
          {{ copied ? COPIED_LABEL : COPY_LABEL }}
        </button>
      </div>
    </header>

    <p v-if="errorText" class="pp-error" role="status">{{ errorText }}</p>

    <p v-if="isStaff && staffStation && !needsStationPick" class="pp-staff-bar">
      <span>你在 <b>{{ staffStationLabel }}</b></span>
      <button type="button" class="pp-staff-switch" @click="requestStationChange">换档口</button>
    </p>

    <section v-if="needsStationPick" class="pp-pick">
      <p class="pp-pick-title">先选你在哪个档口</p>
      <p class="pp-pick-hint">选了只看得到这个档口的参考值。</p>
      <div class="pp-pick-chips">
        <button
          v-for="station in allStations"
          :key="station.id"
          type="button"
          class="pp-pick-chip"
          @click="pickStation(station.id)"
        >
          {{ station.label }}
        </button>
      </div>
    </section>

    <p v-else-if="state === 'loading'" class="pp-empty">正在按现在到打烊算…</p>
    <p v-else-if="closed" class="pp-empty">今天已打烊，明天早上再看。</p>
    <p v-else-if="!groups.length" class="pp-empty">今天不用做。</p>

    <template v-else>
      <nav v-if="stationTabs.length > 1 && !isStaff" class="pp-modes" role="tablist" aria-label="档口">
        <button
          v-for="tab in stationTabs"
          :key="tab.id"
          type="button"
          class="pp-mode"
          :class="{ 'is-active': tab.id === activeStation }"
          role="tab"
          :aria-selected="tab.id === activeStation"
          @click="activeStation = tab.id"
        >
          {{ tab.label }} <b>{{ tab.remaining }}</b>
        </button>
      </nav>

      <section v-for="group in groups" :key="group.kind" class="pp-group">
        <h2 class="pp-group-title">{{ group.label }}</h2>
        <ul class="pp-rows">
          <li v-for="entry in group.entries" :key="entry.id" class="pp-row">
            <button type="button" class="pp-row-main" @click="toggleExpand(entry)">
              <span class="pp-name">{{ entry.item_name }}</span>
              <span class="pp-caret" aria-hidden="true">{{ expandedId === entry.id ? '▾' : '▸' }}</span>
            </button>

            <input
              v-if="editingId === entry.id"
              v-model="editingValue"
              class="pp-qty-input"
              type="number"
              inputmode="decimal"
              min="0"
              step="0.1"
              :aria-label="`${entry.item_name}的数量`"
              @keyup.enter="commitEdit(entry)"
              @blur="commitEdit(entry)"
              @keyup.esc="cancelEdit"
            />
            <button
              v-else
              type="button"
              class="pp-qty"
              :disabled="isStaff"
              @click="!isStaff && startEdit(entry)"
            >
              <b>{{ qtyText(entry) }}</b>
              <i v-if="isManual(entry)" class="pp-manual">手改</i>
            </button>

            <button
              v-if="!isStaff"
              type="button"
              class="pp-check"
              :disabled="busyEntryId === entry.id"
              :aria-label="`${entry.item_name}做完了`"
              @click="toggleDone(entry)"
            >
              做完
            </button>

            <div v-if="expandedId === entry.id" class="pp-detail">
              <div class="pp-chart">
                <MiniBarChart :points="barPoints(entry)" />
              </div>

              <p
                v-if="entry.basis?.status === 'ok' && entry.basis.weekdays?.length"
                class="pp-chart-cap"
              >
                同星期几
              </p>
              <ul v-if="entry.basis?.status === 'ok' && entry.basis.weekdays?.length" class="pp-days">
                <li v-for="row in entry.basis.weekdays" :key="row.day">
                  <span>{{ row.day.slice(5) }}</span>
                  <b>{{ row.qty }}</b>
                </li>
              </ul>
              <p v-if="entry.basis?.status === 'ok'" class="pp-basis">{{ basisFormula(entry) }}</p>
              <p v-else class="pp-basis">{{ basisText(entry) }}</p>

              <div v-if="!isStaff" class="pp-actions">
                <router-link class="pp-act" :to="{ path: '/admin' }">改配方系数</router-link>
                <button
                  type="button"
                  class="pp-act"
                  @click="setKind(entry, entry.kind === 'made' ? 'bought' : 'made')"
                >
                  {{ entry.kind === 'made' ? '其实是买的' : '其实是做的' }}
                </button>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <section v-if="doneInStation.length" class="pp-group pp-done">
        <h2 class="pp-group-title">已做（{{ doneInStation.length }}）</h2>
        <ul class="pp-done-rows">
          <li v-for="entry in doneInStation" :key="entry.id" class="pp-row is-done">
            <button type="button" class="pp-row-main" @click="toggleExpand(entry)">
              <span class="pp-name">{{ entry.item_name }}</span>
            </button>
            <span class="pp-qty"><b>{{ qtyText(entry) }}</b></span>
            <button v-if="!isStaff" type="button" class="pp-check" @click="toggleDone(entry)">
              撤回
            </button>
          </li>
        </ul>
      </section>

      <footer class="pp-foot">
        <span class="pp-muted">{{ BASIS_HINT }}</span>
      </footer>
    </template>
  </div>
</template>

<style scoped>
.pp {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3, 12px);
  padding: var(--sp-2, 8px) var(--sp-3, 12px);
  width: 100%;
  min-width: 0;
  max-width: 980px;
  box-sizing: border-box;
  overflow-x: clip;
  margin: 0 auto;
  color: var(--ink);
}

.pp-head {
  display: flex;
  min-width: 0;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-3, 12px);
  flex-wrap: wrap;
}

.pp-kicker {
  margin: 0 0 2px;
  font-size: var(--fs-12, 12px);
  color: var(--ink-dim);
  letter-spacing: var(--ls-2, 0.08em);
}

.pp-title {
  margin: 0;
  font-size: var(--fs-20, 20px);
  font-weight: 600;
}

.pp-title b {
  font-size: var(--fs-24, 24px);
  color: var(--action);
}

.pp-head-side {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--sp-2, 8px);
}

.pp-window {
  font-size: var(--fs-12, 12px);
  color: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}

.pp-copy {
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-1, 8px);
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-12, 12px);
}

.pp-tabs {
  display: flex;
  min-width: 0;
  gap: var(--sp-2, 8px);
  overflow-x: auto;
  padding-bottom: 2px;
  scrollbar-width: none;
}

.pp-tabs::-webkit-scrollbar {
  display: none;
}

.pp-tab {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: var(--r-full, 999px);
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-13, 13px);
}

.pp-tab.is-on {
  background: var(--action);
  border-color: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}

.pp-tab b {
  font-size: var(--fs-12, 12px);
  padding: 0 6px;
  border-radius: var(--r-full, 999px);
  background: none;
}

.pp-group-title {
  margin: var(--sp-2, 8px) 0 var(--sp-1, 4px);
  font-size: var(--fs-12, 12px);
  font-weight: 600;
  letter-spacing: var(--ls-2, 0.08em);
  color: var(--ink-dim);
}

.pp-rows,
.pp-done-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.pp-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: var(--sp-2, 8px);
  min-height: 56px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}

.pp-row-main:focus {
  outline: none;
}

.pp-row-main:focus-visible {
  outline: 1px solid var(--line-control);
  outline-offset: 2px;
  border-radius: var(--r-1);
}

.pp-row-main {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  min-width: 0;
  min-height: 44px;
  border: 0;
  background: none;
  color: inherit;
  text-align: left;
}

.pp-name {
  flex: 0 1 auto;
  min-width: 0;
  font-size: var(--fs-14, 14px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pp-qty {
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
  min-width: 58px;
  min-height: 44px;
  justify-content: flex-end;
  border: 0;
  background: none;
  color: inherit;
  font-variant-numeric: tabular-nums;
}

.pp-qty b {
  font-size: var(--fs-16, 16px);
  font-weight: 600;
}

.pp-manual {
  font-size: var(--fs-11, 11px);
  font-style: normal;
  color: var(--warn);
}

.pp-qty-input {
  width: 84px;
  min-height: 44px;
  padding: 0 8px;
  border: 1px solid var(--action);
  border-radius: var(--r-1, 8px);
  background: var(--surface-2);
  color: var(--ink);
  font-size: var(--fs-16, 16px);
  text-align: right;
}

.pp-check {
  width: 44px;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1, 8px);
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: var(--fs-12, 12px);
}

.pp-check:active {
  background: var(--action-soft);
  color: var(--action);
}

.pp-detail {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 2px 0 12px 6px;
  padding: 10px 0 2px 14px;
  border-left: 2px solid var(--line-strong);
}

.pp-caret {
  flex: 0 0 auto;
  font-size: var(--fs-11, 11px);
  color: var(--ink-faint);
}

.pp-chart {
  display: flex;
  align-items: flex-start;
  width: 100%;
  color: var(--ink-dim);
}

.pp-chart-cap {
  margin: 2px 0 -4px;
  font-size: var(--fs-11, 11px);
  color: var(--ink-faint);
  letter-spacing: var(--ls-2);
}

.pp-days {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  list-style: none;
  margin: 0;
  padding: 0;
}

.pp-days li {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 9px;
  border-radius: var(--r-1);
  background: var(--surface-2);
  font-size: var(--fs-11, 11px);
  color: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}

.pp-days b {
  font-size: var(--fs-13, 13px);
  font-weight: 600;
  color: var(--ink);
}

.pp-basis {
  margin: 0;
  font-size: var(--fs-11, 11px);
  color: var(--ink-faint);
}

.pp-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 2px;
}

.pp-act {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-full);
  background: none;
  color: var(--ink-dim);
  font-size: var(--fs-12, 12px);
  text-decoration: none;
}

.pp-act:hover {
  border-color: var(--ink-faint);
  color: var(--ink);
}

.pp-pick {
  padding: var(--sp-4, 16px);
  border: 1px solid var(--line);
  border-radius: var(--r-2, 12px);
  background: var(--surface-1);
}

.pp-pick-title {
  margin: 0 0 4px;
  font-size: var(--fs-14, 14px);
  font-weight: 600;
}

.pp-pick-hint {
  margin: 0 0 12px;
  font-size: var(--fs-12, 12px);
  color: var(--ink-dim);
}

.pp-pick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2, 8px);
}

.pp-pick-chip {
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--line-control);
  border-radius: var(--r-full, 999px);
  background: var(--surface-2);
  color: var(--ink);
  font-size: var(--fs-13, 13px);
}

.pp-empty {
  padding: var(--sp-6, 24px) var(--sp-4, 16px);
  border: 1px solid var(--line);
  border-radius: var(--r-2, 12px);
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-13, 13px);
  text-align: center;
}

.pp-staff-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

.pp-staff-bar b {
  color: var(--ink);
}

.pp-staff-switch {
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-full);
  background: var(--surface-1);
  color: var(--ink-dim);
  font-size: var(--fs-12);
}

.pp-modes {
  display: flex;
  gap: 4px;
  padding: 3px;
  border-radius: var(--r-full);
  background: var(--surface-2);
  align-self: flex-start;
}

.pp-mode {
  min-height: 44px;
  padding: 0 16px;
  border: 0;
  border-radius: var(--r-full);
  background: none;
  color: var(--ink-dim);
  font-size: var(--fs-13, 13px);
}

.pp-mode.is-on {
  background: var(--surface-0);
  color: var(--ink);
  font-weight: 600;
}

.pp-restock,
.pp-review {
  list-style: none;
  margin: 0;
  padding: 0;
}

.pp-expired {
  list-style: none;
  margin: 0 0 12px;
  padding: 10px 12px;
  /* 令牌层没有 `--danger-line`，边框取 danger 自己的淡档；底色用既有的 danger-soft。
     **不写 fallback 字面量**：令牌层（`html[data-workbench]`）保证这两个值都有，
     fallback 只会变成第二份硬编码色值 —— 设计护栏（`designGuard.test.js`）会红。 */
  border: 1px solid var(--wb-danger-a45);
  background: var(--danger-soft);
  border-radius: 10px;
}

.pp-expired li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0;
  font-size: var(--fs-13, 13px);
}

.pp-expired-text {
  flex: 1;
  min-width: 0;
}

.pp-expired-text em {
  margin-left: 6px;
  font-style: normal;
  color: var(--danger-bright);
}

.pp-expired-btn {
  flex: 0 0 auto;
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid var(--wb-danger-a45);
  background: var(--surface-0);
  color: var(--danger-bright);
  font-size: var(--fs-12, 12px);
  cursor: pointer;
}

.pp-restock-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}

.pp-restock-main {
  flex: 1 1 140px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pp-restock-main b {
  font-size: var(--fs-14, 14px);
}

.pp-restock-main span {
  font-size: var(--fs-11, 11px);
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}

.pp-restock-form {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.pp-act-primary {
  border-color: var(--action);
  color: var(--action);
}

.pp-reason {
  min-height: 44px;
  padding: 0 8px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink);
  font-size: var(--fs-12, 12px);
}

.pp-review-sum {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: baseline;
  padding: 8px 0;
  font-size: var(--fs-12, 12px);
  color: var(--ink-dim);
}

.pp-review-sum b {
  color: var(--ink);
}

.pp-review-sum em {
  margin-left: auto;
  font-style: normal;
  color: var(--ink-faint);
}

.pp-review-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2px 8px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}

.pp-review-nums {
  grid-column: 1 / 2;
  font-size: var(--fs-11, 11px);
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}

.pp-review-delta {
  grid-row: 1 / 3;
  grid-column: 2 / 3;
  align-self: center;
  font-style: normal;
  font-size: var(--fs-13, 13px);
  color: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}

.pp-review-delta.is-off {
  color: var(--warn);
  font-weight: 600;
}

.pp-error {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--danger);
  border-radius: var(--r-1, 8px);
  color: var(--danger-bright);
  font-size: var(--fs-12, 12px);
}

.pp-foot {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-2, 8px);
  padding: 4px 0 12px;
  font-size: var(--fs-11, 11px);
  color: var(--ink-faint);
  flex-wrap: wrap;
}

.pp-muted {
  color: var(--ink-faint);
}

@media (min-width: 720px) {
  .pp-row {
    grid-template-columns: minmax(0, 1fr) 120px 96px;
  }
}
</style>
