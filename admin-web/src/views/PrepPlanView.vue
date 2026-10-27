<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MiniBarChart from '../components/prep/MiniBarChart.vue'
import { api } from '../api/client'
import { usePrepPlan } from '../composables/usePrepPlan'
import { BASIS_HINT, COPIED_LABEL, COPY_LABEL, PREP_PLAN_TITLE } from '../utils/prepPlanCopy'
import { buildPrepPlanText } from '../utils/prepPlanText'
import { workbenchDocumentTitle } from '../utils/workbenchCopy'

/**
 * 备货计划（ADR 0117 重建）。
 *
 * 这一页只回答一个问题：**今天从现在到打烊，这个档口还要做多少、还要买多少。**
 * 没有时间窗选择器，没有「先点刷新」的空态，没有信心 / 裕度 / 风险读数。
 */

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
const modeBar = ref(null)

const staffStationLabel = computed(
  () => allStations.value.find((row) => row.id === staffStation.value)?.label || staffStation.value,
)

const title = PREP_PLAN_TITLE
workbenchDocumentTitle(title)

const windowText = computed(() => {
  if (!windowEnd.value) return ''
  const clock = windowEnd.value.slice(11, 16)
  return `现在 → 明天 ${clock}（未来 24 小时）`
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

/** 参考值为 0 的品：数据照列（不替店长删信息），但整行降权，别和真要做的抢眼睛。 */
function isZero(entry) {
  return roundQty(entry.final_qty ?? entry.suggested_qty) === 0
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

/**
 * 档口栏在手机上横滑（6 个档口一行放不下）。默认档口可能落在视野外，
 * 换完档口若不把它滑回来，师傅看到的是一排**都没有高亮**的档口 —— 不知道自己在看哪档。
 * 只改该栏的 scrollLeft，不动页面纵向滚动。
 */
async function revealActiveStation() {
  await nextTick()
  const bar = modeBar.value
  const active = bar && bar.querySelector('.pp-mode.is-on')
  if (!bar || !active) return
  const barBox = bar.getBoundingClientRect()
  const box = active.getBoundingClientRect()
  if (box.left >= barBox.left && box.right <= barBox.right) return
  bar.scrollLeft += box.left - barBox.left - (barBox.width - box.width) / 2
}

onMounted(async () => {
  await load()
  void revealActiveStation()
})

watch(activeStation, () => {
  void load()
  void revealActiveStation()
})
</script>

<template>
  <div class="pp">
    <header class="pp-head">
      <div class="pp-head-main">
        <p class="pp-kicker">备货计划 · 参考值</p>
        <h1 class="pp-title">
          今天参考值
          <template v-if="!needsStationPick"><b>{{ remainingCount }}</b> 项</template>
        </h1>
        <p class="pp-sub">
          {{ windowText }}
          <span v-if="doneCount" class="pp-sub-done"> · 已做 {{ doneCount }} 项</span>
        </p>
      </div>
      <div class="pp-head-side">
        <button
          type="button"
          class="pp-copy"
          :disabled="state !== 'ready' || needsStationPick"
          @click="copyList"
        >
          {{ copied ? COPIED_LABEL : COPY_LABEL }}
        </button>
      </div>
    </header>

    <p v-if="errorText" class="pp-error" role="status">{{ errorText }}</p>

    <p v-if="isStaff && staffStation && !needsStationPick" class="pp-staff-bar">
      <span>你在 <b>{{ staffStationLabel }}</b></span>
      <button type="button" class="pp-staff-switch" @click="requestStationChange">换岗位</button>
    </p>

    <section v-if="needsStationPick" class="pp-pick">
      <p class="pp-pick-title">先选你在哪个岗位</p>
      <p class="pp-pick-hint">选了只看得到这个岗位的参考值。</p>
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
    <p v-else-if="!groups.length" class="pp-empty">今天不用做。</p>

    <template v-else>
      <nav
        v-if="stationTabs.length > 1 && !isStaff"
        ref="modeBar"
        class="pp-modes"
        role="tablist"
        aria-label="档口"
      >
        <button
          v-for="tab in stationTabs"
          :key="tab.id"
          type="button"
          class="pp-mode"
          :class="{ 'is-on': tab.id === activeStation }"
          role="tab"
          :aria-selected="tab.id === activeStation"
          @click="activeStation = tab.id"
        >
          {{ tab.label }}<b>{{ tab.remaining }}</b>
        </button>
      </nav>

      <section v-for="group in groups" :key="group.kind" class="pp-group">
        <h2 class="pp-group-title">{{ group.label }}</h2>
        <ul class="pp-rows">
          <li
            v-for="entry in group.entries"
            :key="entry.id"
            class="pp-row"
            :class="{ 'is-zero': isZero(entry), 'is-staff': isStaff }"
            @click="isStaff && toggleExpand(entry)"
          >
            <!-- `.stop`：员工端整行（`li`）也挂了同一个展开动作。点名称时事件会先到这颗按钮、
                 再冒泡到行上执行第二次 —— 两次 toggle 互相抵消，实测表现是「点名称没反应，
                 只有点数字（穿透到行上）才开」。按钮自己停掉冒泡，每条路径就只toggle一次。 -->
            <button type="button" class="pp-row-main" @click.stop="toggleExpand(entry)">
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
              <b>{{ roundQty(entry.final_qty ?? entry.suggested_qty) }}</b>
              <small v-if="entry.unit" class="pp-qty-unit">{{ entry.unit }}</small>
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

            <div v-if="expandedId === entry.id" class="pp-detail" @click.stop>
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
                  {{ entry.kind === 'made' ? '其实是原料' : '其实是半成品' }}
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
            <span class="pp-qty">
              <b>{{ roundQty(entry.final_qty ?? entry.suggested_qty) }}</b>
              <small v-if="entry.unit" class="pp-qty-unit">{{ entry.unit }}</small>
            </span>
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

/* 副标题（时间窗 / 已做几项）：先前没有类规则，吃浏览器默认的 `p` 样式 —— 16px 墨色，
   比 20px 标题只小一档，整页第一眼是「三层白字」。这里压回 12px 的 dim 档。 */
.pp-sub {
  margin: 2px 0 0;
  font-size: var(--fs-12, 12px);
  color: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}

.pp-sub-done {
  color: var(--ink-faint);
}

.pp-head-side {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--sp-2, 8px);
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

.pp-copy:disabled {
  opacity: 0.55;
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
  min-height: 60px;
  padding: 6px 0;
  /* 分隔线用 `--line-control`（`.55`）而不是默认的 `--line`（`.13`）：这一页是**长清单**，
     行与行只靠一条 0.13 的青线，叠在壳的网格背景上等于没有 —— 手机上整片糊成一块，
     分不清哪一行是哪一项。控件的强分隔档在深底上是 2.5:1 上下的细线：看得清、不抢内容。 */
  border-bottom: 1px solid var(--line-control);
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

/* 单位比数字小一号、压一档色：`59.4条` 整串 16px 粗体时，量词和数量一样重，
   六行看下来是一片白字。0 值行降到 faint —— 参考值是 0 说明今天不用备。 */
.pp-qty-unit {
  font-size: var(--fs-11, 11px);
  color: var(--ink-dim);
}

.pp-row.is-staff {
  cursor: pointer;
}

.pp-row.is-zero .pp-name,
.pp-row.is-done .pp-name {
  color: var(--ink-dim);
}

.pp-row.is-done .pp-qty b,
.pp-row.is-done .pp-qty-unit {
  color: var(--ink-faint);
}

.pp-row.is-zero .pp-qty b,
.pp-row.is-zero .pp-qty-unit {
  color: var(--ink-faint);
}

.pp-manual {
  font-size: var(--fs-11, 11px);
  font-style: normal;
  color: var(--warn);
}

/* 员工端的数量是只读的 disabled 按钮：Chrome 对 disabled 元素不派发点击，事件也冒泡不上去。
   指针穿透让「点数字」跟「点这一行」一样能展开近 7 天依据 —— 师傅第一反应就是点数字。 */
.pp-qty:disabled {
  pointer-events: none;
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
  font-size: var(--fs-12, 12px);
  color: var(--ink-dim);
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

/* 档口筛选：容器是一条可以横着滑的胶囊栏。
   手机上 6 个档口在 390px 里放不下，旧写法没有 `flex:0 0 auto` + 横向滚动，每个按钮被
   压缩到 53–60px，档口名竖着断成「西饼 / 档 / 16」三行。现在按钮不缩、一行不折，放不下就横滑。 */
.pp-modes {
  display: flex;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: var(--r-full);
  background: var(--surface-2);
  align-self: flex-start;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scrollbar-width: none;
}

.pp-modes::-webkit-scrollbar {
  display: none;
}

.pp-mode {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: var(--r-full);
  background: none;
  color: var(--ink-dim);
  font-size: var(--fs-13, 13px);
  white-space: nowrap;
}

/* 选中态：v3 把档口选择复用了 v2 的视图 tab（模板给 `is-active`、样式写 `is-on`），
   于是**任何一个档口都没有高亮**——切成哪个档只看得到数字变。类名对齐在模板里，
   这里用 action-soft 底 + 淡边框标出来（不用实底：一排 6 个里一个亮绿太抢）。 */
.pp-mode.is-on {
  border-color: var(--wb-tint-22);
  background: var(--action-soft);
  color: var(--ink);
  font-weight: 600;
}

.pp-mode b {
  font-size: var(--fs-12, 12px);
  font-weight: 600;
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}

.pp-mode.is-on b {
  color: var(--action);
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
    /* 第三列 = `.pp-check` 的 44px。旧值 96px 让按钮不贴右，行尾多出 52px 死白：
       页头「复制清单」贴右、列表按钮却缩进一截，整页右边缘对不齐。 */
    grid-template-columns: minmax(0, 1fr) 120px 44px;
  }
}
</style>
