<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MiniBarChart from '../components/prep/MiniBarChart.vue'
import { api } from '../api/client'
import { usePrepPlan } from '../composables/usePrepPlan'
import {
  BROKEN_RULE_TEXT,
  COPIED_LABEL,
  COPY_LABEL,
  HORIZON_OPTIONS,
  MAJOR_TAG,
  MISSING_RULE_TEXT,
  NO_HISTORY_TEXT,
  PREP_PLAN_TITLE,
  STALE_FALLBACK_TEXT,
  basisHint,
  horizonLabel,
  horizonTitle,
  loadingText,
} from '../utils/prepPlanCopy'
import { buildPrepPlanText } from '../utils/prepPlanText'
import { workbenchDocumentTitle } from '../utils/workbenchCopy'

/**
 * 备货计划（ADR 0117 重建）。
 *
 * 这一页只回答一个问题：**从现在起的未来 N 小时，这个档口还要做多少、还要买多少。**
 * N 由页头的跨度胶囊选（24 / 48 / 72 小时 / 7 天），窗口始终从「现在」起算，
 * 所以同一时刻切到 48 小时看到的是滚动 48 小时，不是「今天 + 明天两个整天」。
 * 没有「先点刷新」的空态，没有信心 / 裕度 / 风险读数。
 */

const {
  state,
  errorText,
  windowEnd,
  closed,
  horizonHours,
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
  setHorizon,
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

/** 终点离今天几天：窗口是「现在起 N 小时」，48 小时可能落在明天、也可能后天。 */
function endDayOffset(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso))
  if (!match) return 0
  const end = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  const now = new Date()
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.round((end - today) / 86400000)
}

/** 页头那句「现在 → 明天 13:59（未来 24 小时）」。超过后天就写日期，别数「大后天」。 */
const windowText = computed(() => {
  if (!windowEnd.value) return ''
  const clock = windowEnd.value.slice(11, 16)
  if (!clock) return ''
  const offset = endDayOffset(windowEnd.value)
  const day =
    offset <= 0 ? '今天' : offset === 1 ? '明天' : offset === 2 ? '后天' : windowEnd.value.slice(5, 10).replace('-', '/')
  return `现在 → ${day} ${clock}（未来 ${horizonLabel(horizonHours.value)}）`
})

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

/** 用 UTC 构造再取星期，避免手机时区把日期挪到前一天。 */
function weekdayOf(day) {
  const parts = String(day || '').split('-').map(Number)
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return ''
  return WEEKDAYS[new Date(Date.UTC(parts[0], parts[1] - 1, parts[2])).getUTCDay()]
}

/** 近 8 天用量（今天 + 前面 7 个完整营业日）：后端给的是「今天往前」的顺序，画图要从早到晚。 */
function barPoints(entry) {
  const history = Array.isArray(entry.history) ? entry.history : []
  return history
    .map((row) => ({
      value: Number(row.qty || 0),
      label: String(row.day || '').slice(5),
      weekday: weekdayOf(row.day),
    }))
    .reverse()
}

/** 同星期几的候选日用量：后端按「最近往前」给，画图从早到晚。 */
function basisPoints(entry) {
  const rows = Array.isArray(entry.basis?.weekdays) ? entry.basis.weekdays : []
  return rows
    .map((row) => ({
      value: Number(row.qty || 0),
      label: String(row.day || '').slice(5),
      weekday: weekdayOf(row.day),
    }))
    .reverse()
}

/** 统计量的写法：整数就写整数，带小数留 1 位。分位落点走这个，不走「整份」口径。 */
function statText(value) {
  const num = Number(value) || 0
  return Number.isInteger(num) ? String(num) : num.toFixed(1)
}

/** 均值写法与柱形图一致（1 位小数），给 caption 用。 */
function historyMean(entry) {
  const rows = Array.isArray(entry.history) ? entry.history : []
  if (!rows.length) return 0
  const total = rows.reduce((sum, row) => sum + (Number(row.qty) || 0), 0)
  return total / rows.length
}

/**
 * 同星期几柱形图的标题。多段（48 小时以上）时那根柱是「整窗口合计」，虚线是各段的
 * 合计落点，标题就不写数字——「几段相加」写在下面的说明里，免得同一个数出现两遍。
 */
function weekdayCaption(entry) {
  const basis = entry.basis || {}
  const weeks = (basis.weekdays || []).length
  const tag = quantileTag(entry)
  if (isMultiSlice(basis)) return `同星期几（近 ${weeks} 周，整窗口合计）· ${tag} 合计`
  return `同星期几（近 ${weeks} 周，同钟点区间）· ${tag} ${statText(basis.quantile_qty)}`
}

/** 参考值是不是按「每 24 小时一段」加起来算的（48 小时以上）。 */
function isMultiSlice(basis) {
  return Array.isArray(basis?.slices) && basis.slices.length > 1
}

/** 这一品用的档位标签（p50 / p70），柱形图虚线与说明共用。 */
function quantileTag(entry) {
  const pct = Math.round(Number(entry.basis?.quantile ?? 0.5) * 100)
  return `p${pct}`
}

/** 数量收口：半成品只能整份做（后端也算成整数，这里再兜一层，翻旧单也不会冒出 58.7 份），
 *  原料保留 1 位小数 —— 0.5 斤是真的能称出来的，抹成 0 就没人买了。 */
function roundQty(value, kind) {
  const number = Number(value) || 0
  return kind === 'made' ? Math.ceil(number) : Math.round(number * 10) / 10
}

/** 这一行显示的数字（手改过的优先）。 */
function entryQty(entry) {
  return roundQty(entry.final_qty ?? entry.suggested_qty, entry.kind)
}

function qtyText(entry) {
  return `${entryQty(entry)}${entry.unit || ''}`
}

/** 参考值为 0 的品：数据照列（不替店长删信息），但整行降权，别和真要做的抢眼睛。 */
function isZero(entry) {
  return entryQty(entry) === 0
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
  // 半成品输入框本来就该按整份收，手打 2.4 也按 3 份算（后端同样向上取整，两边一致）
  await setQty(entry, entry.kind === 'made' ? Math.ceil(Number(next)) : next)
}

function cancelEdit() {
  editingId.value = null
}

/**
 * 展开后的公式说明：分位档 → 整店水位，几段都写出来。
 * ADR 0128 起分位按品分档（大品 p70 / 其余 p50），basis.quantile 存的是这一品实际用的档。
 */
function basisFormula(entry) {
  const basis = entry.basis || {}
  const days = (basis.weekdays || []).length
  const pct = Math.round(Number(basis.quantile ?? 0.5) * 100)
  const factor = Number(basis.store_factor || 1)
  const tier = basis.is_major ? `p${pct}，${MAJOR_TAG}` : `p${pct}`
  const slices = Array.isArray(basis.slices) ? basis.slices : []
  const parts = []
  if (isMultiSlice(basis)) {
    // 多段：把「每天各多少、加起来多少」写出来，用户要的就是这个加法
    const each = slices.map((part) => statText(part.quantile_qty)).join(' + ')
    parts.push(
      `${slices.length} 段相加：${each} = ${statText(basis.quantile_qty)}（每段各取 ${tier}）`,
    )
  } else {
    // 单段：落点数字在上面那张图的 caption 里（「· p50 16」），这句只说规则
    parts.push(`这 ${days} 天同样钟点里偏保守的那档（${tier}）`)
  }
  if (basis.fallback_applied) parts.push(STALE_FALLBACK_TEXT)
  if (Math.abs(factor - 1) >= 0.05) parts.push(`× 整店水位 ${factor.toFixed(2)}`)
  return parts.join(' ')
}

function basisText(entry) {
  const basis = entry.basis || {}
  if (basis.status === 'missing_rule') return MISSING_RULE_TEXT
  if (basis.status === 'no_history') return NO_HISTORY_TEXT
  if (basis.status === 'broken_rule') return BROKEN_RULE_TEXT
  const rows = (basis.weekdays || []).map((row) => `${row.day.slice(5)} ${row.qty}`)
  const base = rows.length ? `近 ${rows.length} 个同星期几：${rows.join(' / ')}` : ''
  const extras = []
  if (basis.is_major) extras.push(MAJOR_TAG)
  const factor = Number(basis.store_factor || 1)
  if (Math.abs(factor - 1) >= 0.05) extras.push(`整店水位 ×${factor}`)
  return [base, ...extras].filter(Boolean).join('；')
}

async function copyList() {
  const text = buildPrepPlanText({
    windowEnd: windowEnd.value,
    horizonHours: horizonHours.value,
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
          {{ horizonTitle(horizonHours) }}参考值
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

    <nav class="pp-ranges" role="tablist" aria-label="参考值时间跨度">
      <button
        v-for="option in HORIZON_OPTIONS"
        :key="option.hours"
        type="button"
        class="pp-range"
        :class="{ 'is-on': option.hours === horizonHours }"
        role="tab"
        :aria-selected="option.hours === horizonHours"
        @click="setHorizon(option.hours)"
      >
        {{ option.label }}
      </button>
    </nav>

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

    <p v-else-if="state === 'loading'" class="pp-empty">{{ loadingText(horizonHours) }}</p>
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
            :class="{
              'is-zero': isZero(entry),
              'is-staff': isStaff,
              'is-broken': entry.basis?.status === 'broken_rule',
            }"
            @click="isStaff && toggleExpand(entry)"
          >
            <!-- `.stop`：员工端整行（`li`）也挂了同一个展开动作。点名称时事件会先到这颗按钮、
                 再冒泡到行上执行第二次 —— 两次 toggle 互相抵消，实测表现是「点名称没反应，
                 只有点数字（穿透到行上）才开」。按钮自己停掉冒泡，每条路径就只toggle一次。 -->
            <button type="button" class="pp-row-main" @click.stop="toggleExpand(entry)">
              <span class="pp-name">{{ entry.item_name }}</span>
              <i v-if="entry.basis?.is_major" class="pp-major">{{ MAJOR_TAG }}</i>
              <i v-if="entry.basis?.status === 'broken_rule'" class="pp-broken">配方没接上</i>
              <span class="pp-caret" aria-hidden="true">{{ expandedId === entry.id ? '▾' : '▸' }}</span>
            </button>

            <input
              v-if="editingId === entry.id"
              v-model="editingValue"
              class="pp-qty-input"
              type="number"
              :inputmode="entry.kind === 'made' ? 'numeric' : 'decimal'"
              min="0"
              :step="entry.kind === 'made' ? 1 : 0.1"
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
              <b>{{ entryQty(entry) }}</b>
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
              <div class="pp-charts">
                <figure class="pp-chart pp-chart--history">
                  <figcaption class="pp-chart-cap">
                    近 {{ entry.history?.length || 8 }} 天（今天只算到此刻）· 均
                    {{ statText(historyMean(entry)) }}
                  </figcaption>
                  <MiniBarChart
                    :points="barPoints(entry)"
                    :aria-label="`近 ${entry.history?.length || 8} 天用量`"
                  />
                </figure>
                <figure
                  v-if="entry.basis?.status === 'ok' && entry.basis.weekdays?.length"
                  class="pp-chart pp-chart--weekday"
                >
                  <figcaption class="pp-chart-cap">
                    {{ weekdayCaption(entry) }}
                  </figcaption>
                  <MiniBarChart
                    :points="basisPoints(entry)"
                    tone="alt"
                    :line="entry.basis.quantile_qty"
                    :line-label="quantileTag(entry)"
                    :highlight-last="false"
                    :aria-label="`近 ${entry.basis.weekdays.length} 周同星期几用量，${quantileTag(entry)} 落在 ${entry.basis.quantile_qty}`"
                  />
                </figure>
              </div>
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
              <b>{{ entryQty(entry) }}</b>
              <small v-if="entry.unit" class="pp-qty-unit">{{ entry.unit }}</small>
            </span>
            <button v-if="!isStaff" type="button" class="pp-check" @click="toggleDone(entry)">
              撤回
            </button>
          </li>
        </ul>
      </section>

      <footer class="pp-foot">
        <span class="pp-muted">{{ basisHint(horizonHours) }}</span>
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

/* 大品标记：只标「这一档取的是 p70」，不参与排版权重 —— 它解释数字为什么偏高，
   让人不用点开就知道哪几项是重点。 */
.pp-major {
  flex: 0 0 auto;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--line);
  color: var(--ink-dim);
  font-size: var(--fs-11, 11px);
  font-style: normal;
  line-height: 16px;
}

/* 配方断档：参考值算不出来但实际在卖，这一行就是「今天少备一天货」的入口。 */
.pp-broken {
  flex: 0 0 auto;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--fs-11, 11px);
  font-style: normal;
  line-height: 16px;
}

.pp-row.is-broken .pp-name {
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

.pp-charts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 22px;
}

.pp-chart {
  display: flex;
  flex: 1 1 300px;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  margin: 0;
  color: var(--ink-dim);
}

.pp-chart-cap {
  margin: 0;
  font-size: var(--fs-11, 11px);
  color: var(--ink-faint);
  letter-spacing: var(--ls-2);
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

/* 时间跨度：页头下面一排胶囊，四个都放得下；选中态与档口栏同一套（action-soft 底 + 淡边框）。
   切换会重算整份清单，所以按钮保持 44px 触控高度，别做成小字链接。 */
.pp-ranges {
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

.pp-ranges::-webkit-scrollbar {
  display: none;
}

.pp-range {
  flex: 0 0 auto;
  min-height: 44px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: var(--r-full);
  background: none;
  color: var(--ink-dim);
  font-size: var(--fs-13, 13px);
  white-space: nowrap;
}

.pp-range.is-on {
  border-color: var(--wb-tint-22);
  background: var(--action-soft);
  color: var(--ink);
  font-weight: 600;
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
