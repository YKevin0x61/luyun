<script setup>
import { computed, onMounted, ref } from 'vue'
import LuyunDatePicker from '../components/ui/LuyunDatePicker.vue'
import { api } from '../api/client'
import { useStationsStore } from '../stores/stations'
import { workbenchDocumentTitle } from '../utils/workbenchCopy'
import { PREP_REVIEW_EMPTY, PREP_REVIEW_KICKER, PREP_REVIEW_TITLE } from '../utils/prepPlanCopy'

/**
 * 备货复盘（ADR 0123 / 0129）。
 *
 * 只回答一个问题：**那一次给的参考值，事后看准不准。**
 *
 * 口径的关键：一条快照 = 一个 24 小时窗口的一次预测，只能跟**它自己那个窗口**的实际
 * 销量比。同一天的 24 条快照窗口高度重叠（相邻两条差 1 小时），把它们平均起来误差会
 * 互相抵消——逐条全错也能平均出「偏差很小」。所以这一页按**快照钟点**分组，
 * 默认看 07:00（师傅真正下单备货的那一次），其余钟点收在下面，用来观察「越晚越不准」。
 */

workbenchDocumentTitle('备货复盘')

const stations = useStationsStore()
const day = ref(new Date().toLocaleDateString('sv-SE'))
const state = ref('idle')
const errorText = ref('')
const snapshots = ref([])
const primaryClock = ref(null)
const clock = ref(null)
const showOtherClocks = ref(false)

const current = computed(() => snapshots.value.find((g) => g.clock === clock.value) || null)

const currentItems = computed(() => current.value?.items || [])

const otherClocks = computed(() => snapshots.value.filter((g) => g.clock !== clock.value))

function pct(value) {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function num(value, digits = 1) {
  if (value === null || value === undefined) return '—'
  return Number(value).toFixed(digits)
}

function signed(value) {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}`
}

function verdict(item) {
  if (item.sold_qty === null || item.sold_qty === undefined) return { text: '窗口没结束', kind: 'pending' }
  const gap = item.sold_qty - item.suggested_qty
  if (Math.abs(gap) < 0.05) return { text: '刚好', kind: 'even' }
  if (gap > 0) return { text: `缺 ${num(gap)}`, kind: 'short' }
  return { text: `多 ${num(-gap)}`, kind: 'over' }
}

function stationName(id) {
  return id ? stations.nameOf(id) : '未分类'
}

function tier(item) {
  if (item.is_major) return 'p70 大品'
  return item.quantile === null || item.quantile === undefined ? '—' : `p${Math.round(item.quantile * 100)}`
}

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    const data = await api.get(`/api/prep-plan/snapshots?day=${day.value}`)
    snapshots.value = data.snapshots || []
    primaryClock.value = data.primary_clock || snapshots.value[0]?.clock || null
    clock.value = primaryClock.value
    state.value = 'ready'
  } catch (err) {
    state.value = 'error'
    errorText.value = err.message || '快照读不出来'
  }
}

onMounted(() => {
  stations.load().catch(() => {})
  load()
})
</script>

<template>
  <div class="pr">
    <header class="pr-head">
      <div>
        <p class="pr-kicker">{{ PREP_REVIEW_KICKER }}</p>
        <h1 class="pr-title">{{ PREP_REVIEW_TITLE }}</h1>
      </div>
      <label class="pr-date">
        <span>日期</span>
        <LuyunDatePicker v-model="day" placeholder="选择日期" aria-label="复盘日期" @update:model-value="load" />
      </label>
    </header>

    <p v-if="errorText" class="pr-error" role="status">{{ errorText }}</p>
    <p v-else-if="state === 'loading'" class="pr-empty">正在读快照…</p>
    <p v-else-if="!snapshots.length" class="pr-empty">{{ PREP_REVIEW_EMPTY }}</p>

    <template v-else>
      <div class="pr-clocks" role="tablist" aria-label="快照钟点">
        <button
          v-for="group in snapshots"
          :key="group.clock"
          type="button"
          role="tab"
          class="pr-clock"
          :class="{ 'is-on': group.clock === clock, 'is-primary': group.clock === primaryClock }"
          :aria-selected="group.clock === clock"
          @click="clock = group.clock"
        >
          {{ group.clock }}
        </button>
      </div>

      <template v-if="current">
        <p class="pr-line">
          <b>{{ current.clock }}</b> 这一版参考值：实际卖出 {{ num(current.summary.sold_total) }}，参考合计
          {{ num(current.summary.suggested_total) }}，参考 ÷ 实际
          <b>{{ num(current.summary.ref_ratio, 2) }}</b>
          <span v-if="current.summary.pending" class="pr-dim">
            · 还有 {{ current.summary.pending }} 个窗口没结束（要等 24 小时）
          </span>
          <span v-if="current.summary.drifted" class="pr-warn">
            · {{ current.summary.drifted }} 行的配方在这之后改过，实际销量按现在的配方重算
          </span>
        </p>

        <div class="pr-metrics">
          <div class="pr-metric">
            <p class="pr-metric-k">够卖</p>
            <p class="pr-metric-v" :class="current.summary.covered >= 0.7 ? 'is-ok' : 'is-warn'">
              {{ pct(current.summary.covered) }}
            </p>
            <p class="pr-metric-h">{{ current.summary.measured }} 个品里，参考值 ≥ 实际的有多少</p>
          </div>
          <div class="pr-metric">
            <p class="pr-metric-k">缺货</p>
            <p class="pr-metric-v is-short">{{ pct(current.summary.shortfall_ratio) }}</p>
            <p class="pr-metric-h">少做了 {{ num(current.summary.shortfall) }}，占实际销量</p>
          </div>
          <div class="pr-metric">
            <p class="pr-metric-k">多出</p>
            <p class="pr-metric-v is-over">{{ pct(current.summary.overfill_ratio) }}</p>
            <p class="pr-metric-h">多做/买了 {{ num(current.summary.overfill) }}，占实际销量</p>
          </div>
        </div>

        <table class="pr-table">
          <thead>
            <tr>
              <th>备货品</th>
              <th>档口</th>
              <th>档位</th>
              <th class="n">参考值</th>
              <th class="n">实际卖出</th>
              <th class="n">偏差</th>
              <th class="n">判定</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in currentItems" :key="item.item_name + item.unit">
              <td>
                {{ item.item_name }}<span class="pr-unit">{{ item.unit }}</span>
                <span v-if="item.status === 'broken_rule'" class="pr-warn">配方没接上</span>
              </td>
              <td class="pr-dim">{{ stationName(item.station) }}</td>
              <td class="pr-dim">{{ tier(item) }}</td>
              <td class="n">{{ num(item.suggested_qty) }}</td>
              <td class="n">{{ item.sold_qty === null ? '—' : num(item.sold_qty) }}</td>
              <td class="n" :class="item.sold_qty === null ? '' : item.sold_qty > item.suggested_qty ? 'is-short' : 'is-over'">
                {{ item.sold_qty === null ? '—' : signed(item.sold_qty - item.suggested_qty) }}
              </td>
              <td class="n">
                <span class="pr-verdict" :class="`is-${verdict(item).kind}`">{{ verdict(item).text }}</span>
              </td>
            </tr>
          </tbody>
        </table>

        <details class="pr-others">
          <summary>其他钟点（{{ otherClocks.length }} 个）——用来看「越晚越不准」</summary>
          <table class="pr-table pr-table-compact">
            <thead>
              <tr>
                <th>快照钟点</th>
                <th class="n">够卖</th>
                <th class="n">缺货</th>
                <th class="n">多出</th>
                <th class="n">参考 ÷ 实际</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="group in otherClocks" :key="group.clock">
                <td>
                  <button type="button" class="pr-link" @click="clock = group.clock">{{ group.clock }}</button>
                </td>
                <td class="n">{{ pct(group.summary.covered) }}</td>
                <td class="n is-short">{{ pct(group.summary.shortfall_ratio) }}</td>
                <td class="n is-over">{{ pct(group.summary.overfill_ratio) }}</td>
                <td class="n">{{ num(group.summary.ref_ratio, 2) }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </template>
    </template>
  </div>
</template>

<style scoped>
.pr {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-4);
  width: 100%;
  min-width: 0;
}

.pr-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-3);
}

.pr-kicker {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

.pr-title {
  margin: 2px 0 0;
  font-size: var(--fs-20);
}

.pr-date {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-13);
  color: var(--ink-dim);
  white-space: nowrap;
  flex: none;
}

/* 132px 会把 2026-11-03 截成 2026-11-0：日期框 + 清空按钮一起量 150px */
.pr-date :deep(.luyun-date-picker) {
  width: 150px;
}

.pr-date :deep(.luyun-date-picker .dp--input) {
  width: 100%;
  font: inherit;
  font-size: var(--fs-13);
}

.pr-empty,
.pr-error,
.pr-line {
  margin: 0;
  font-size: var(--fs-13);
  color: var(--ink-dim);
}

.pr-error {
  color: var(--danger-bright);
}

/* 24 个钟点铺三行会把「指标卡」挤出首屏：一行横向滚，手指划一下就到 */
.pr-clocks {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
  padding-bottom: 2px;
}

.pr-clocks::-webkit-scrollbar {
  display: none;
}

.pr-clock {
  flex: none;
}

.pr-clock {
  padding: 3px 9px;
  border: 1px solid var(--line);
  border-radius: var(--r-full);
  background: transparent;
  color: var(--ink-dim);
  font: inherit;
  font-size: var(--fs-12);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.pr-clock.is-on {
  border-color: var(--action);
  background: var(--action-soft);
  color: var(--ink);
}

.pr-clock.is-primary:not(.is-on) {
  border-color: var(--line-strong);
  color: var(--ink);
}

.pr-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-3);
}

.pr-metric {
  padding: var(--sp-3);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  background: var(--surface-2);
}

.pr-metric-k {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--ink-dim);
}

.pr-metric-v {
  margin: 4px 0 2px;
  font-size: var(--fs-24);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.pr-metric-v.is-ok {
  color: var(--ok);
}

.pr-metric-v.is-warn {
  color: var(--warn);
}

.pr-metric-v.is-short {
  color: var(--danger-bright);
}

.pr-metric-v.is-over {
  color: var(--warn);
}

.pr-metric-h {
  margin: 0;
  font-size: var(--fs-11);
  color: var(--ink-faint);
}

.pr-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-13);
}

.pr-table th {
  text-align: left;
  font-weight: 600;
  font-size: var(--fs-12);
  color: var(--ink-dim);
  border-bottom: 1px solid var(--line);
  padding: 8px 10px;
  white-space: nowrap;
}

.pr-table td {
  border-bottom: 1px solid var(--line);
  padding: 7px 10px;
}

.pr-table .n {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.pr-table-compact td,
.pr-table-compact th {
  padding: 5px 8px;
}

.pr-unit {
  margin-left: 3px;
  font-size: var(--fs-11);
  color: var(--ink-dim);
}

.pr-dim {
  color: var(--ink-dim);
}

.pr-warn {
  margin-left: 6px;
  font-size: var(--fs-11);
  color: var(--warn);
}

.is-short {
  color: var(--danger-bright);
}

.is-over {
  color: var(--warn);
}

.pr-verdict {
  font-size: var(--fs-12);
}

.pr-verdict.is-short {
  color: var(--danger-bright);
}

.pr-verdict.is-over {
  color: var(--warn);
}

.pr-verdict.is-even,
.pr-verdict.is-pending {
  color: var(--ink-faint);
}

.pr-others summary {
  cursor: pointer;
  font-size: var(--fs-13);
  color: var(--ink-dim);
  padding: 6px 0;
}

.pr-link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--info);
  font: inherit;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

@media (max-width: 720px) {
  .pr {
    padding: var(--sp-3);
  }

  /* 手机上指标不缩成一行三列：24px 的数字挤在 1/3 屏宽里会换行 */
  .pr-metrics {
    grid-template-columns: minmax(0, 1fr);
  }

  .pr-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .pr-table {
    font-size: var(--fs-12);
  }
}
</style>
