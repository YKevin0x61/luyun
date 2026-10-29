<script setup>
import { computed, onMounted, ref } from 'vue'
import LuyunDatePicker from '../components/ui/LuyunDatePicker.vue'
import { api } from '../api/client'
import { workbenchDocumentTitle } from '../utils/workbenchCopy'

/**
 * 备货复盘（ADR 0123）。
 *
 * 只回答一个问题：**每小时给的参考值，事后看准不准。**
 * 数据来自 `prep_plan_snapshots`（预测侧，历史事实）与 `prep_plan_actuals`（实际侧，
 * 窗口结束后回填）。两边都存配方指纹 —— 不一致说明改过配方，对比不可靠，页面上标出来。
 */

workbenchDocumentTitle('备货复盘')

const day = ref(new Date().toLocaleDateString('sv-SE'))
const state = ref('idle')
const errorText = ref('')
const items = ref([])

const rows = computed(() =>
  items.value.map((item) => {
    const hours = item.hours || []
    const latest = hours[hours.length - 1] || {}
    const withActual = hours.filter((h) => h.sold_qty !== null && h.sold_qty !== undefined)
    const avgSuggested =
      withActual.length ? withActual.reduce((sum, h) => sum + h.suggested_qty, 0) / withActual.length : null
    const avgSold =
      withActual.length ? withActual.reduce((sum, h) => sum + (h.sold_qty || 0), 0) / withActual.length : null
    const mismatch = withActual.some((h) => h.same_rules === false)
    return {
      ...item,
      latestQty: latest.suggested_qty ?? 0,
      hoursCount: hours.length,
      avgSuggested,
      avgSold,
      delta: avgSold === null || avgSuggested === null ? null : avgSold - avgSuggested,
      mismatch,
    }
  }),
)

const summary = computed(() => {
  const done = rows.value.filter((r) => r.delta !== null)
  if (!done.length) return null
  const over = done.filter((r) => r.delta > 0).length
  const under = done.filter((r) => r.delta < 0).length
  return { total: done.length, over, under }
})

function num(value, digits = 1) {
  if (value === null || value === undefined) return '—'
  return Number(value).toFixed(digits)
}

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    const data = await api.get(`/api/prep-plan/snapshots?day=${day.value}`)
    items.value = data.items || []
    state.value = 'ready'
  } catch (err) {
    state.value = 'error'
    errorText.value = err.message || '快照读不出来'
  }
}

onMounted(load)
</script>

<template>
  <div class="pr">
    <header class="pr-head">
      <div>
        <p class="pr-kicker">备货复盘</p>
        <h1 class="pr-title">参考值 vs 实际销量</h1>
      </div>
      <label class="pr-date">
        <span>日期</span>
        <!-- 站内日期组件（2026-10-13 全站统一口径）：原生 `<input type=date>` 的显示
             格式跟**浏览器界面语言**走（中文机 `yyyy/mm/dd`、英文机 `mm/dd/yyyy`）。 -->
        <LuyunDatePicker
          v-model="day"
          placeholder="选择日期"
          aria-label="复盘日期"
          @update:model-value="load"
        />
      </label>
    </header>

    <p v-if="errorText" class="pr-error" role="status">{{ errorText }}</p>
    <p v-else-if="state === 'loading'" class="pr-empty">正在读快照…</p>
    <p v-else-if="!rows.length" class="pr-empty">
      这天还没有快照。整点快照从部署后开始累积，回填要等窗口结束（24 小时）后才有实际销量。
    </p>

    <template v-else>
      <p v-if="summary" class="pr-summary">
        有实际数据的 {{ summary.total }} 个品里，实际高于参考值 {{ summary.over }} 个、低于 {{ summary.under }} 个
      </p>

      <table class="pr-table">
        <thead>
          <tr>
            <th>备货品</th>
            <th>档口</th>
            <th class="n">最新参考值</th>
            <th class="n">参考均值</th>
            <th class="n">实际均值</th>
            <th class="n">偏差</th>
            <th>快照数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.item_name + row.unit">
            <td>
              {{ row.item_name }}<span class="pr-unit">{{ row.unit }}</span>
              <span v-if="row.mismatch" class="pr-warn">配方改过，对比不可靠</span>
            </td>
            <td>{{ row.station || '未分类' }}</td>
            <td class="n">{{ num(row.latestQty) }}</td>
            <td class="n">{{ num(row.avgSuggested) }}</td>
            <td class="n">{{ num(row.avgSold) }}</td>
            <td class="n" :class="row.delta > 0 ? 'pr-up' : row.delta < 0 ? 'pr-down' : ''">
              {{ row.delta === null ? '—' : (row.delta > 0 ? '+' : '') + num(row.delta) }}
            </td>
            <td>{{ row.hoursCount }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </div>
</template>

<style scoped>
.pr {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px;
  width: 100%;
  min-width: 0;
}

.pr-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}

.pr-kicker {
  margin: 0;
  font-size: 12px;
  color: var(--ink-dim);
}

.pr-title {
  margin: 2px 0 0;
  font-size: 20px;
}

.pr-date {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ink-dim);
}

.pr-date input {
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  font: inherit;
}

/* 日期组件替掉原生 date（2026-10-13 全站统一口径）之后尺寸要自己补：上面那几条
   `.pr-date input` 只作用于原生控件，组件渲染的是 `.dp--input`。宽度给死，别让它在
   flex 行里把页头撑开；底色 / 边框 / 文字色由共享表给。 */
.pr-date :deep(.luyun-date-picker .dp--input) {
  width: 132px;
  font: inherit;
  font-size: 13px;
}

.pr-summary,
.pr-empty,
.pr-error {
  margin: 0;
  font-size: 13px;
  color: var(--ink-dim);
}

.pr-error {
  color: var(--danger-bright);
}

.pr-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.pr-table th {
  text-align: left;
  font-weight: 600;
  font-size: 12px;
  color: var(--ink-dim);
  border-bottom: 1px solid var(--line);
  padding: 8px 10px;
  white-space: nowrap;
}

.pr-table td {
  border-bottom: 1px solid var(--line);
  padding: 8px 10px;
}

.pr-table .n {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.pr-unit {
  margin-left: 4px;
  font-size: 11px;
  color: var(--ink-dim);
}

.pr-warn {
  margin-left: 8px;
  font-size: 11px;
  color: var(--warn);
}

.pr-up {
  color: var(--danger-bright);
}

.pr-down {
  color: var(--ok);
}
</style>
