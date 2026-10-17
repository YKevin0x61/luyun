<script setup>
/**
 * 管理端的「损耗分析」页（从 worktool2 那个小程序的 `pages/wastage/stats` + `list` 搬过来，
 * 两页合一页：上面看数、下面翻明细）。
 *
 * 四样汇总：本月**总笔数**、**物料种类**、**原因分布**、**高损耗物料 TOP 5**、**逐日趋势**
 * —— 口径全在服务端（`services/wastage/store.py` 的 `statistics`），页面只画。
 *
 * 明细那一半带三把筛子（范围 / 原因 / 关键词）与分页，每行能点开照片。范围默认跟**当前
 * 看的那个月**走，也可以切成近 30 / 60 天或全部 —— 源项目那张独立列表页的常用范围。
 *
 * 页面清单里 `audience: admin` + `staffCap: wastage`（2026-10-14 用户裁定）：管理端会话照旧
 * 直接进；员工会话**勾了「损耗分析」这一项能力**的也进这一页（看全店统计与含「谁记的」的
 * 明细）。其余员工手输 URL 落 `/workbench/forbidden`；接口那一侧 `/statistics` 对他们的
 * 员工会话回 403 —— 页面能不能打开与接口能不能读是两件事，两处都判。
 * 员工自己登记与看自己那几笔仍走登记页（`/workbench/me/loss`）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import { api } from '../../api/client.js'
import { loginRedirectTarget } from '../../utils/loginNext'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { WASTAGE_REGISTER_PATH } from '../../utils/wastagePaths'

const router = useRouter()

const PAGE_SIZE = 20

const state = ref('loading') // loading | ready | error
const errorText = ref('')
const today = ref('')
const month = ref('')
const stats = ref(null)
const statsLoading = ref(false)

const detailMode = ref('month') // month | last30 | last60 | all
const detailReason = ref('')
const detailKeyword = ref('')
const records = ref([])
const detailTotal = ref(0)
const offset = ref(0)
const detailLoading = ref(false)
const reasons = ref([])

const monthLabel = computed(() => {
  const [year, mon] = String(month.value || '').split('-')
  return year && mon ? `${year}年${Number(mon)}月` : ''
})
const isCurrentMonth = computed(() => month.value >= String(today.value).slice(0, 7))
const maxCount = computed(() => Math.max(1, ...(stats.value?.daily_stats || []).map((r) => r.count), 0))

function redirectLogin() {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
}

function shiftMonth(value, delta) {
  const [year, mon] = String(value || '').split('-').map(Number)
  if (!year || !mon) return value
  const total = year * 12 + (mon - 1) + delta
  const nextYear = Math.floor(total / 12)
  const nextMon = (total % 12) + 1
  return `${nextYear}-${String(nextMon).padStart(2, '0')}`
}

function shiftDay(value, delta) {
  const [year, mon, day] = String(value || '').split('-').map(Number)
  if (!year || !mon || !day) return value
  const date = new Date(Date.UTC(year, mon - 1, day))
  date.setUTCDate(date.getUTCDate() + delta)
  return date.toISOString().slice(0, 10)
}

function monthEnd(value) {
  const [year, mon] = String(value || '').split('-').map(Number)
  if (!year || !mon) return ''
  const days = new Date(Date.UTC(year, mon, 0)).getUTCDate()
  return `${year}-${String(mon).padStart(2, '0')}-${String(days).padStart(2, '0')}`
}

/** 明细的日期范围：默认跟当前看的月份走，另外三档是源项目列表页的常用窗口。 */
const detailRange = computed(() => {
  if (detailMode.value === 'all') return { start: '', end: '' }
  if (detailMode.value === 'month' && month.value) {
    return { start: `${month.value}-01`, end: monthEnd(month.value) }
  }
  const days = detailMode.value === 'last30' ? 30 : 60
  return { start: shiftDay(today.value, -(days - 1)), end: today.value }
})

const rangeHint = computed(() => {
  const { start, end } = detailRange.value
  if (!start || !end) return '全部时间'
  return `${start} ~ ${end}`
})

/** 逐日趋势的柱子：补齐整月每一天（没有记录的那天是 0，不是「断掉」）。
 *
 *  几何在 viewBox 的 0-100 单位里算，不写内联 style —— 柱子的 `x/y/width/height`
 *  是 SVG **属性**绑定，形状由 `<rect>` 自己画（设计护栏不许新文件写内联样式）。
 */
const dailyBars = computed(() => {
  const current = stats.value
  if (!current || !current.start_date) return []
  const counts = Object.fromEntries((current.daily_stats || []).map((row) => [row.date, row.count]))
  const prefix = current.start_date.slice(0, 7)
  const days = Number(current.end_date.slice(8, 10))
  const peak = maxCount.value
  const slot = 100 / days
  const width = slot * 0.62
  return Array.from({ length: days }, (_, index) => {
    const day = index + 1
    const date = `${prefix}-${String(day).padStart(2, '0')}`
    const count = counts[date] || 0
    const height = count ? Math.max(6, (count / peak) * 100) : 0
    return {
      day,
      date,
      count,
      x: Number((index * slot + (slot - width) / 2).toFixed(2)),
      w: Number(width.toFixed(2)),
      y: Number((100 - height).toFixed(2)),
      h: Number(height.toFixed(2)),
    }
  })
})
const hasTrend = computed(() => dailyBars.value.some((bar) => bar.count > 0))
const topItems = computed(() => (stats.value?.top_items || []).slice(0, 5))
const pageCount = computed(() => Math.max(1, Math.ceil(detailTotal.value / PAGE_SIZE)))
const pageIndex = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)

function formatQuantity(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  return String(Math.round(number * 1000) / 1000)
}

async function loadStats() {
  if (!month.value) return
  statsLoading.value = true
  errorText.value = ''
  try {
    stats.value = await api.get('/api/wastage/statistics', { month: month.value })
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    errorText.value = err.message || '读不到统计'
  } finally {
    statsLoading.value = false
  }
}

async function loadDetail(reset = false) {
  if (reset) offset.value = 0
  detailLoading.value = true
  try {
    const { start, end } = detailRange.value
    const data = await api.get('/api/wastage/records', {
      start_date: start,
      end_date: end,
      reason: detailReason.value,
      keyword: detailKeyword.value.trim(),
      offset: offset.value,
      limit: PAGE_SIZE,
    })
    records.value = data.records || []
    detailTotal.value = data.total || 0
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    errorText.value = err.message || '读不到明细'
  } finally {
    detailLoading.value = false
  }
}

async function reload() {
  await Promise.all([loadStats(), loadDetail(true)])
}

async function turnMonth(delta) {
  const next = shiftMonth(month.value, delta)
  if (delta > 0 && next > String(today.value).slice(0, 7)) return
  month.value = next
  if (detailMode.value === 'month') {
    await reload()
  } else {
    await loadStats()
  }
}

async function onMonthPick(value) {
  // 月份组件允许清空（加班台账那一页的清空 = 看全部）。这一页的统计**必须**落在某个月
  // 上，所以清空一律回落到本月 —— 而不是让输入框空着、数据还是上一个月。
  month.value = value || String(today.value).slice(0, 7)
  await reload()
}

async function setDetailMode(mode) {
  detailMode.value = mode
  await loadDetail(true)
}

async function search() {
  await loadDetail(true)
}

async function turnPage(delta) {
  const next = offset.value + delta * PAGE_SIZE
  if (next < 0 || next >= detailTotal.value) return
  offset.value = next
  await loadDetail()
}

onMounted(async () => {
  document.title = workbenchDocumentTitle('损耗分析')
  try {
    const meta = await api.get('/api/wastage/meta')
    today.value = meta.today
    month.value = String(meta.today).slice(0, 7)
    reasons.value = meta.meta.reasons
    state.value = 'ready'
  } catch (err) {
    if (err.status === 401) {
      redirectLogin()
      return
    }
    errorText.value = err.message || '读不到损耗登记的资料'
    state.value = 'error'
    return
  }
  await reload()
})
</script>

<template>
  <div class="loss-page">
    <p v-if="state === 'loading'" class="loss-muted">正在读损耗数据…</p>

    <template v-else-if="state === 'error'">
      <p class="loss-error" role="alert">{{ errorText }}</p>
      <button class="btn btn-block" type="button" @click="reload()">重试</button>
    </template>

    <template v-else>
      <div class="loss-head">
        <div class="loss-month">
          <button class="btn btn-sm" type="button" aria-label="上一个月" @click="turnMonth(-1)">‹</button>
          <LuyunDatePicker
            :model-value="month"
            month-picker
            aria-label="统计哪个月"
            @update:model-value="onMonthPick"
          />
          <button
            class="btn btn-sm"
            type="button"
            aria-label="下一个月"
            :disabled="isCurrentMonth"
            @click="turnMonth(1)"
          >›</button>
        </div>
        <router-link class="btn btn-sm" :to="WASTAGE_REGISTER_PATH">去登记一笔</router-link>
      </div>

      <p v-if="errorText" class="loss-error" role="alert">{{ errorText }}</p>

      <section class="loss-cards">
        <div class="loss-stat">
          <span class="loss-stat-label">本月总损耗</span>
          <b class="loss-stat-value">{{ stats?.total_count ?? 0 }}<em>次</em></b>
        </div>
        <div class="loss-stat">
          <span class="loss-stat-label">涉及物料</span>
          <b class="loss-stat-value">{{ stats?.item_type_count ?? 0 }}<em>种</em></b>
        </div>
        <div class="loss-stat">
          <span class="loss-stat-label">占最多的是</span>
          <b class="loss-stat-value loss-stat-text">{{ stats?.by_reason?.[0]?.reason_text || '—' }}</b>
        </div>
      </section>

      <section class="loss-block">
        <div class="loss-block-hd"><span>原因分布</span><span class="loss-hint">{{ monthLabel }}</span></div>
        <p v-if="!stats?.total_count" class="loss-muted">这个月还没有损耗记录。</p>
        <ul v-else class="loss-bars">
          <li v-for="row in stats.by_reason" :key="row.reason" class="loss-bar-row">
            <span class="loss-bar-name">{{ row.reason_text }}</span>
            <svg
              class="loss-bar-track"
              viewBox="0 0 100 8"
              preserveAspectRatio="none"
              role="img"
              :aria-label="`${row.reason_text} ${row.count} 次，占 ${row.percentage}%`"
            >
              <rect class="loss-bar-base" x="0" y="0" width="100" height="8" />
              <rect class="loss-bar-fill" x="0" y="0" :width="row.percentage" height="8" />
            </svg>
            <span class="loss-bar-num">{{ row.count }} 次 · {{ row.percentage }}%</span>
          </li>
        </ul>
      </section>

      <section class="loss-block">
        <div class="loss-block-hd"><span>高损耗物料</span><span class="loss-hint">TOP 5</span></div>
        <p v-if="!topItems.length" class="loss-muted">这个月还没有物料损耗记录。</p>
        <ol v-else class="loss-rank">
          <li v-for="(row, index) in topItems" :key="`${row.item_name}-${row.unit}`" class="loss-rank-row">
            <span class="loss-rank-no" :class="{ top: index === 0 }">{{ index + 1 }}</span>
            <span class="loss-rank-name">{{ row.item_name }}</span>
            <span class="loss-rank-num">
              {{ row.count }} 次 · 共 {{ formatQuantity(row.total_quantity) }}{{ row.unit }}
            </span>
          </li>
        </ol>
      </section>

      <section class="loss-block">
        <div class="loss-block-hd"><span>日损耗趋势</span><span class="loss-hint">按当天记了几笔</span></div>
        <p v-if="!hasTrend" class="loss-muted">这个月还没有趋势数据。</p>
        <svg
          v-else
          class="loss-trend"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          role="img"
          :aria-label="`${monthLabel}逐日损耗笔数`"
        >
          <rect
            v-for="bar in dailyBars"
            :key="bar.date"
            class="loss-trend-bar"
            :x="bar.x"
            :y="bar.y"
            :width="bar.w"
            :height="bar.h"
          >
            <title>{{ bar.date }}：{{ bar.count }} 笔</title>
          </rect>
        </svg>
        <div v-if="hasTrend" class="loss-trend-axis">
          <span v-for="bar in dailyBars" :key="bar.date" class="loss-trend-tick">
            {{ bar.day === 1 || bar.day % 5 === 0 ? bar.day : '' }}
          </span>
        </div>
      </section>

      <section class="loss-block">
        <div class="loss-block-hd">
          <span>明细</span>
          <span class="loss-hint">{{ rangeHint }} · 共 {{ detailTotal }} 笔</span>
        </div>

        <div class="loss-filters">
          <div class="loss-modes">
            <button
              v-for="mode in [
                { key: 'month', label: '这个月' },
                { key: 'last30', label: '近30天' },
                { key: 'last60', label: '近60天' },
                { key: 'all', label: '全部' },
              ]"
              :key="mode.key"
              type="button"
              class="loss-mode"
              :class="{ on: detailMode === mode.key }"
              @click="setDetailMode(mode.key)"
            >{{ mode.label }}</button>
          </div>
          <div class="loss-filter-row">
            <select v-model="detailReason" class="loss-input loss-filter-reason" @change="search">
              <option value="">全部原因</option>
              <option v-for="row in reasons" :key="row.value" :value="row.value">{{ row.label }}</option>
            </select>
            <input
              v-model="detailKeyword"
              class="loss-input"
              type="search"
              placeholder="搜物料名"
              @keyup.enter="search"
            />
            <button class="btn btn-sm" type="button" @click="search">筛</button>
          </div>
        </div>

        <p v-if="detailLoading" class="loss-muted">正在读明细…</p>
        <p v-else-if="!records.length" class="loss-muted">这个范围内没有记录。</p>
        <ul v-else class="loss-list">
          <li v-for="row in records" :key="row.id" class="loss-item">
            <div class="loss-item-hd">
              <span class="loss-date">{{ row.wastage_date }}</span>
              <b class="loss-name">{{ row.item_name }}</b>
              <span class="loss-qty">{{ formatQuantity(row.quantity) }}{{ row.unit }}</span>
              <span class="loss-reason-tag">{{ row.reason_text }}</span>
              <span class="loss-who">{{ row.operator_name }}</span>
            </div>
            <p v-if="row.remark" class="loss-remark">{{ row.remark }}</p>
            <div v-if="row.images && row.images.length" class="loss-thumbs">
              <a
                v-for="shot in row.images"
                :key="shot.capture_id"
                :href="shot.url"
                target="_blank"
                rel="noopener"
              >
                <img :src="shot.url" alt="现场照片" />
              </a>
            </div>
          </li>
        </ul>

        <div v-if="detailTotal > PAGE_SIZE" class="loss-pager">
          <button class="btn btn-sm" type="button" :disabled="offset === 0" @click="turnPage(-1)">上一页</button>
          <span class="loss-hint">第 {{ pageIndex }} / {{ pageCount }} 页</span>
          <button
            class="btn btn-sm"
            type="button"
            :disabled="offset + PAGE_SIZE >= detailTotal"
            @click="turnPage(1)"
          >下一页</button>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.loss-page { padding: 12px 14px 28px; }
.loss-muted { font-size: 13px; color: var(--ink-dim); }
.loss-error { font-size: 13px; color: var(--danger-bright); }
.loss-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; margin-bottom: 14px;
}
.loss-month { display: flex; align-items: center; gap: 8px; }
.loss-month .btn { min-width: 32px; justify-content: center; }
/* 「去登记一笔」不许折行：它是一颗入口按钮，两行会把页头撑高。 */
.loss-head > .btn { white-space: nowrap; }
.loss-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin-bottom: 14px; }
.loss-stat {
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 4px;
}
.loss-stat-label { font-size: 11.5px; color: var(--ink-dim); }
.loss-stat-value { font-size: 22px; font-variant-numeric: tabular-nums; }
.loss-stat-value em { font-style: normal; font-size: 12px; color: var(--ink-dim); margin-left: 3px; }
.loss-stat-text { font-size: 16px; }
.loss-block {
  border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 14px;
}
.loss-block-hd {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  font-size: 13px; letter-spacing: .06em; color: var(--ink); margin-bottom: 10px;
}
.loss-hint { font-size: 11px; letter-spacing: 0; color: var(--ink-dim); }
.loss-bars { list-style: none; margin: 0; padding: 0; display: grid; gap: 9px; }
.loss-bar-row { display: grid; grid-template-columns: 72px 1fr auto; align-items: center; gap: 9px; }
.loss-bar-name { font-size: 12.5px; }
.loss-bar-track { width: 100%; height: 8px; display: block; overflow: hidden; }
.loss-bar-base { fill: var(--surface-3); }
.loss-bar-fill { fill: var(--action); }
.loss-bar-num { font-size: 11.5px; color: var(--ink-dim); font-variant-numeric: tabular-nums; }
.loss-rank { list-style: none; margin: 0; padding: 0; display: grid; gap: 7px; }
.loss-rank-row { display: flex; align-items: center; gap: 9px; }
.loss-rank-no {
  width: 20px; height: 20px; flex: none; border-radius: 50%; font-size: 11px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--line); color: var(--ink-dim);
}
.loss-rank-no.top { border-color: var(--action); color: var(--action); }
.loss-rank-name { font-size: 13.5px; flex: 1 1 auto; }
.loss-rank-num { font-size: 11.5px; color: var(--ink-dim); font-variant-numeric: tabular-nums; }
.loss-trend { display: block; width: 100%; height: 96px; }
.loss-trend-bar { fill: var(--action); }
.loss-trend-axis { display: flex; gap: 2px; margin-top: 3px; }
.loss-trend-tick {
  flex: 1 1 0; text-align: center; font-size: 9.5px; color: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}
.loss-filters { display: grid; gap: 9px; margin-bottom: 10px; }
.loss-modes { display: flex; gap: 6px; flex-wrap: wrap; }
.loss-mode {
  padding: 4px 11px; font-size: 12px; font-family: inherit; cursor: pointer;
  color: var(--ink); background: transparent;
  border: 1px solid var(--line); border-radius: 999px;
}
.loss-mode.on { border-color: var(--action); color: var(--action); }
.loss-filter-row { display: flex; gap: 8px; align-items: center; }
.loss-filter-reason { flex: 0 0 116px; }
.loss-input {
  width: 100%; padding: 7px 10px; font-size: 13px; font-family: inherit;
  color: var(--ink); background: var(--surface-2);
  border: 1px solid var(--line); border-radius: 8px;
}
.loss-list { list-style: none; margin: 0; padding: 0; }
.loss-item { padding: 9px 0; border-top: 1px solid var(--line); }
.loss-list > .loss-item:first-child { border-top: 0; }
.loss-item-hd { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.loss-date { font-size: 12px; color: var(--ink-dim); font-variant-numeric: tabular-nums; }
.loss-name { font-size: 14px; }
.loss-qty { font-size: 13px; color: var(--action); font-variant-numeric: tabular-nums; }
.loss-reason-tag {
  font-size: 11px; padding: 1px 8px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--ink-dim);
}
.loss-who { font-size: 11.5px; color: var(--ink-dim); margin-left: auto; }
.loss-remark { margin: 4px 0 0; font-size: 12px; color: var(--ink-dim); }
.loss-thumbs { display: flex; gap: 6px; margin-top: 6px; }
.loss-thumbs img { width: 46px; height: 46px; object-fit: cover; border-radius: 6px; display: block; }
.loss-pager { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 12px; }
</style>
