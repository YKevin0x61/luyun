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
 * 员工自己登记与看自己那几笔仍走登记页（`/workbench/staff/loss`）。
 *
 * A 批（2026-11）补的是「这一屏的数字是哪一刻的、读不出来时也别装成 0」：
 *   - 30 秒轮询 + 手动刷新，页头写「刚更新 / HH:MM 更新」；标签页藏起来时停表，回来立即刷一次；
 *   - 统计读失败时顶部一条错误条（带服务端的原话与重试），两张卡写「—」，旧数字不清空；
 *   - 本月没有记录时只留一张空态卡，原因/排行/趋势不硬画成 0；
 *   - 趋势图补 y 轴与峰值；换月份时明细范围跟着回「这个月」，口径不一致时抬头写明；
 *   - 首次加载用骨架（不闪），翻页/换范围后滚回明细第一行，照片改成页内灯箱。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import { api } from '../../api/client.js'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_STAFF } from '../../utils/workbenchIdentity'
import { loginRedirectTarget } from '../../utils/loginNext'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { WASTAGE_REGISTER_PATH } from '../../utils/wastagePaths'

const router = useRouter()
const identityStore = useWorkbenchIdentityStore()

/** 「去登记一笔」到底记在谁名下（F 组）：这一页管理端会话与勾了「损耗分析」能力的员工
 *  会话都能进。上一轮那句提示只写了管理端 → 店长（员工会话）看到的是错话：他点过去
 *  登记，记的是**他自己**的名字，而且他自己就是员工，不需要再「去员工端」。 */
const registerIdentityHint = computed(() => {
  if (identityStore.identity !== IDENTITY_STAFF) {
    return '登记会记成「管理端」名义；员工请用员工端「损耗」入口。'
  }
  const name = String(identityStore.staffName || '').trim()
  return name
    ? '登记会记成你自己的名字（' + name + '）；这一页看的是全店统计。'
    : '登记会记成你自己的名字；这一页看的是全店统计。'
})

const PAGE_SIZE = 20
/** 统计与明细的自动轮询间隔（A3）。 */
const POLL_INTERVAL_MS = 30_000
/** 一分钟内不报具体钟点，写「刚更新」——轮询每 30 秒就会推进一次。 */
const FRESH_WINDOW_MS = 60_000

const state = ref('loading') // loading | ready
const metaError = ref('')
const statsError = ref('')
const detailError = ref('')
const today = ref('')
const month = ref('')
const monthTouched = ref(false)
const stats = ref(null)
const statsLoading = ref(false)
const updatedAt = ref(0)
const clockTick = ref(Date.now())

const detailMode = ref('month') // month | last30 | last60 | all
const detailReason = ref('')
const detailKeyword = ref('')
const records = ref([])
const detailTotal = ref(0)
const offset = ref(0)
const detailLoading = ref(false)
const reasons = ref([])

const detailSection = ref(null)
const detailList = ref(null)
const lightboxImage = ref(null)
const lightboxCloseBtn = ref(null)

let pollTimer = null
let bodyOverflowBeforeLightbox = null

const monthLabel = computed(() => {
  const [year, mon] = String(month.value || '').split('-')
  return year && mon ? `${year}年${Number(mon)}月` : ''
})
const isCurrentMonth = computed(() => month.value >= String(today.value).slice(0, 7))
const maxCount = computed(() => Math.max(
  1,
  ...(stats.value?.daily_stats || []).map((row) => Number(row.count) || 0),
  0,
))

const statTotal = computed(() => Number(stats.value?.total_count) || 0)
const statKinds = computed(() => Number(stats.value?.item_type_count) || 0)
/** meta 与 statistics 任何一个读失败都要顶错误条：前者缺原因清单、后者缺全部数字。 */
const summaryError = computed(() => statsError.value || metaError.value)
/** A5：本月一笔都没有（且不是「读失败」）——这时只摆空态卡，不把 0 画成三张表。 */
const isEmptyMonth = computed(() => Boolean(stats.value)
  && !summaryError.value
  && statTotal.value === 0)

function redirectLogin() {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
}

function localToday() {
  const now = new Date()
  const mon = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${mon}-${day}`
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

/** A11：统计按月份、明细按另一段时，把两个口径的关系写在抬头下面。 */
const detailScopeHint = computed(() => {
  if (detailMode.value === 'month') return ''
  const { start, end } = detailRange.value
  const range = start && end ? `${start.slice(5)} ~ ${end.slice(5)}` : '全部时间'
  return `统计按 ${month.value}，明细按 ${range}`
})

/** 逐日趋势的柱子：补齐整月每一天（没有记录的那天是 0，不是「断掉」）。
 *
 *  几何在 viewBox 的 0-100 单位里算，不写内联 style —— 柱子的 `x/y/width/height`
 *  是 SVG **属性**绑定，形状由 `<rect>` 自己画（设计护栏不许新文件写内联样式）。
 */
const dailyBars = computed(() => {
  const current = stats.value
  if (!current || !current.start_date || !current.end_date) return []
  const counts = Object.fromEntries((current.daily_stats || []).map((row) => [row.date, row.count]))
  const prefix = current.start_date.slice(0, 7)
  const days = Number(current.end_date.slice(8, 10))
  if (!days) return []
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
const peakBar = computed(() => dailyBars.value.reduce(
  (best, bar) => (bar.count > (best ? best.count : 0) ? bar : best),
  null,
))
/** A9：趋势卡标题右侧「峰值 N 笔 · MM-DD」。 */
const peakLabel = computed(() => {
  const bar = peakBar.value
  if (!bar || !bar.count) return ''
  return `峰值 ${bar.count} 笔 · ${bar.date.slice(5)}`
})
/** A9：y 轴三档（最大 / 中间 / 0），中间值取半、四舍五入到整笔。
 *  最大只有 1~2 笔时中间值会跟最大重合（1/1/0、2/1/0），去掉重复的一档，别画两个一样的刻度。 */
const trendScale = computed(() => {
  const max = maxCount.value
  const mid = Math.round(max / 2)
  return mid > 0 && mid < max ? [max, mid, 0] : [max, 0]
})

const topItems = computed(() => (stats.value?.top_items || []).slice(0, 5))
/** A8：原来「占最多的是」那张卡并进原因分布卡的标题右侧。 */
const topReasonLabel = computed(() => {
  const row = (stats.value?.by_reason || [])[0]
  if (!row) return ''
  return `最多：${row.reason_text} ${Number(row.count) || 0} 次 · ${Number(row.percentage) || 0}%`
})

const pageCount = computed(() => Math.max(1, Math.ceil(detailTotal.value / PAGE_SIZE)))
const pageIndex = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)

/** A4：明细行上的「改」角标；服务端没给 edit_count（老数据/老接口）时按 0 处理。 */
function editCountOf(row) {
  const count = Number(row?.edit_count)
  return Number.isFinite(count) && count > 0 ? count : 0
}

/** A4：明细抬头的「改过 X 笔 · 撤回 Y 笔」，两项都 0 时不占位置。 */
const amendSummary = computed(() => {
  const edited = Number(stats.value?.edited_count) || 0
  const revoked = Number(stats.value?.revoked_count) || 0
  if (!edited && !revoked) return ''
  return `改过 ${edited} 笔 · 撤回 ${revoked} 笔`
})

/** A3：页头的更新时间。一分钟内写「刚更新」，再久报 HH:MM。 */
const updatedLabel = computed(() => {
  if (!updatedAt.value) return ''
  const age = clockTick.value - updatedAt.value
  if (age < FRESH_WINDOW_MS) return '刚更新'
  const at = new Date(updatedAt.value)
  const hh = String(at.getHours()).padStart(2, '0')
  const mm = String(at.getMinutes()).padStart(2, '0')
  return `${hh}:${mm} 更新`
})

function formatQuantity(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  return String(Math.round(number * 1000) / 1000)
}

/** 读登记页的资料（今天 / 原因清单）。失败也不整页报错：用本机日期兜底，统计照常请求。 */
async function loadMeta() {
  try {
    const meta = await api.get('/api/wastage/meta')
    today.value = meta.today
    // 用户手动挑过月份之后就不再拿服务端的「本月」覆盖他。
    if (!month.value || !monthTouched.value) month.value = String(meta.today).slice(0, 7)
    reasons.value = Array.isArray(meta.meta?.reasons) ? meta.meta.reasons : []
    metaError.value = ''
    return 'ok'
  } catch (err) {
    if (err.status === 401) {
      redirectLogin()
      return 'auth'
    }
    metaError.value = err.message || '读不到损耗登记的资料'
    if (!today.value) today.value = localToday()
    if (!month.value) month.value = today.value.slice(0, 7)
    return 'error'
  }
}

async function loadStats() {
  if (!month.value) return
  statsLoading.value = true
  try {
    stats.value = await api.get('/api/wastage/statistics', { month: month.value })
    statsError.value = ''
    updatedAt.value = Date.now()
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    // 只换错误条，不把 stats 清空 —— 屏上的旧数字还标着是哪一刻读的（A3）。
    statsError.value = err.message || '读不到统计'
  } finally {
    statsLoading.value = false
    clockTick.value = Date.now()
  }
}

async function loadDetail(reset = false) {
  if (reset) offset.value = 0
  detailLoading.value = true
  detailError.value = ''
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
    detailTotal.value = Number(data.total) || 0
    // 轮询/撤回会让总数缩水：当前页可能已经越界，退回最后一页再读一次。
    if (offset.value > 0 && offset.value >= detailTotal.value) {
      offset.value = Math.max(0, (Math.ceil(detailTotal.value / PAGE_SIZE) - 1) * PAGE_SIZE)
      return await loadDetail()
    }
  } catch (err) {
    if (err.status === 401) return redirectLogin()
    detailError.value = err.message || '读不到明细'
  } finally {
    detailLoading.value = false
  }
}

/** 手动刷新与轮询共用：统计 + 明细一起重读，不切回骨架（A12）。 */
async function refreshAll() {
  clockTick.value = Date.now()
  await Promise.all([loadStats(), loadDetail()])
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    if (state.value !== 'ready') return
    if (document.visibilityState === 'hidden') return
    void refreshAll()
  }, POLL_INTERVAL_MS)
}

/** A3：标签页藏起来就停表，回来立即刷一次再重新计时。 */
function onVisibilityChange() {
  if (document.visibilityState === 'hidden') {
    stopPolling()
    return
  }
  startPolling()
  if (state.value === 'ready') void refreshAll()
}

async function manualRefresh() {
  if (statsLoading.value) return
  await refreshAll()
}

/** A6：错误条上的重试。meta 没读到时先补它（原因清单/今天都靠它）。 */
async function retrySummary() {
  const monthBefore = month.value
  if (metaError.value) {
    const result = await loadMeta()
    if (result === 'auth') return
  }
  await loadStats()
  if (month.value !== monthBefore) await loadDetail(true)
}

/** 换月时先把上个月的数清掉：
 *  读新月份失败时旧数字配着新月份抬头会骗人，宁可写「—」+ 错误条（A3 的「保留旧数字」
 *  只适用于同一月份的刷新/轮询）。 */
function resetStatsForMonthChange() {
  stats.value = null
  updatedAt.value = 0
}

async function turnMonth(delta) {
  const next = shiftMonth(month.value, delta)
  if (delta > 0 && next > String(today.value).slice(0, 7)) return
  month.value = next
  monthTouched.value = true
  // A11：统计的月份一换，明细范围回「这个月」，否则上下两块说的不是同一个月。
  detailMode.value = 'month'
  resetStatsForMonthChange()
  await Promise.all([loadStats(), loadDetail(true)])
}

/** A10：非本月时把月份与明细范围一起拉回本月。 */
async function backToCurrentMonth() {
  month.value = String(today.value).slice(0, 7)
  monthTouched.value = true
  detailMode.value = 'month'
  resetStatsForMonthChange()
  await Promise.all([loadStats(), loadDetail(true)])
}

async function onMonthPick(value) {
  // 月份组件允许清空（加班台账那一页的清空 = 看全部）。这一页的统计**必须**落在某个月
  // 上，所以清空一律回落到本月 —— 而不是让输入框空着、数据还是上一个月。
  month.value = value || String(today.value).slice(0, 7)
  monthTouched.value = true
  detailMode.value = 'month'
  resetStatsForMonthChange()
  await Promise.all([loadStats(), loadDetail(true)])
}

/** A14：翻页/换范围后滚回明细第一行（列表还没渲染时退回明细卡本身）。 */
function scrollToDetail() {
  const target = detailList.value || detailSection.value
  if (target && typeof target.scrollIntoView === 'function') {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

async function setDetailMode(mode) {
  detailMode.value = mode
  await loadDetail(true)
  await nextTick()
  scrollToDetail()
}

async function search() {
  await loadDetail(true)
  await nextTick()
  scrollToDetail()
}

async function turnPage(delta) {
  const next = offset.value + delta * PAGE_SIZE
  if (next < 0 || next >= detailTotal.value) return
  offset.value = next
  await loadDetail()
  await nextTick()
  scrollToDetail()
}

/** A14：缩略图不再 `target=_blank` —— 页内遮罩里看原图，Esc / 点遮罩关掉。 */
function openLightbox(shot) {
  if (!shot || !shot.url) return
  bodyOverflowBeforeLightbox = document.body.style.overflow
  lightboxImage.value = { url: shot.url, alt: '现场照片' }
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onLightboxKeydown)
  nextTick(() => {
    if (lightboxCloseBtn.value && typeof lightboxCloseBtn.value.focus === 'function') {
      lightboxCloseBtn.value.focus()
    }
  })
}

function closeLightbox() {
  if (!lightboxImage.value) return
  lightboxImage.value = null
  if (bodyOverflowBeforeLightbox !== null) {
    document.body.style.overflow = bodyOverflowBeforeLightbox
    bodyOverflowBeforeLightbox = null
  }
  window.removeEventListener('keydown', onLightboxKeydown)
}

function onLightboxKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeLightbox()
  }
}

onMounted(async () => {
  document.title = workbenchDocumentTitle('损耗分析')
  document.addEventListener('visibilitychange', onVisibilityChange)
  startPolling()
  const metaResult = await loadMeta()
  if (metaResult === 'auth') return
  await Promise.all([loadStats(), loadDetail(true)])
  state.value = 'ready'
})

onBeforeUnmount(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', onVisibilityChange)
  window.removeEventListener('keydown', onLightboxKeydown)
  if (lightboxImage.value) {
    lightboxImage.value = null
    document.body.style.overflow = bodyOverflowBeforeLightbox ?? ''
    bodyOverflowBeforeLightbox = null
  }
})
</script>

<template>
  <div class="loss-page">
    <template v-if="state === 'loading'">
      <div class="loss-skeleton-cards" aria-hidden="true">
        <div class="loss-skeleton loss-skeleton-card" />
        <div class="loss-skeleton loss-skeleton-card" />
      </div>
      <div class="loss-skeleton loss-skeleton-chart" aria-hidden="true" />
      <p class="loss-muted">正在读损耗数据…</p>
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
        <div class="loss-head-actions">
          <span v-if="updatedLabel" class="loss-updated" aria-live="polite">{{ updatedLabel }}</span>
          <button
            class="btn btn-sm loss-refresh"
            type="button"
            :disabled="statsLoading"
            @click="manualRefresh"
          >刷新</button>
          <button
            v-if="!isCurrentMonth"
            class="btn btn-sm loss-back-current"
            type="button"
            @click="backToCurrentMonth"
          >回到本月</button>
          <router-link
            class="btn btn-sm loss-register"
            :to="WASTAGE_REGISTER_PATH"
            :title="registerIdentityHint"
          >去登记一笔</router-link>
        </div>
      </div>
      <!-- A10：管理端点「去登记一笔」会落到员工区、且归属记成「管理端」。入口留着（要能
           直接补一笔），但话先说在前面，免得点完才发现记的名义不对。 -->
      <p class="loss-hint loss-register-hint">{{ registerIdentityHint }}</p>

      <!-- A6：统计读不出来时把服务端的原话摆出来，并给重试；卡片写「—」而不是 0。 -->
      <div v-if="summaryError" class="loss-error-bar" role="alert">
        <span class="loss-error-title">统计没读出来</span>
        <span class="loss-error-detail">{{ summaryError }}</span>
        <button class="btn btn-sm" type="button" :disabled="statsLoading" @click="retrySummary">重试</button>
      </div>

      <!-- A5：本月一笔都没有时只留这一张卡，不把 0 画成原因表/排行/趋势。 -->
      <section v-if="isEmptyMonth" class="loss-empty" role="status">
        <p class="loss-empty-title">这个月还没有损耗记录</p>
        <div class="loss-empty-actions">
          <router-link class="btn btn-sm" :to="WASTAGE_REGISTER_PATH">去登记一笔</router-link>
          <button class="btn btn-sm loss-empty-prev" type="button" @click="turnMonth(-1)">看上月</button>
        </div>
      </section>

      <template v-else>
        <!-- A8：原来三张卡并成两张；「占最多的是」挪到原因卡的标题右侧。 -->
        <section class="loss-cards">
          <div class="loss-stat">
            <span class="loss-stat-label">本月总损耗</span>
            <b class="loss-stat-value" :class="{ 'loss-stat-empty': !stats }">
              <template v-if="stats">{{ statTotal }}<em>次</em></template>
              <template v-else>—</template>
            </b>
          </div>
          <div class="loss-stat">
            <span class="loss-stat-label">涉及物料</span>
            <b class="loss-stat-value" :class="{ 'loss-stat-empty': !stats }">
              <template v-if="stats">{{ statKinds }}<em>种</em></template>
              <template v-else>—</template>
            </b>
          </div>
        </section>

        <template v-if="stats">
          <section class="loss-block">
            <div class="loss-block-hd">
              <span>原因分布</span>
              <span class="loss-top-reason">{{ topReasonLabel || monthLabel }}</span>
            </div>
            <p v-if="!stats.by_reason || !stats.by_reason.length" class="loss-muted">这个月还没有原因数据。</p>
            <ul v-else class="loss-bars">
              <li v-for="row in stats.by_reason" :key="row.reason" class="loss-bar-row">
                <span class="loss-bar-name">{{ row.reason_text }}</span>
                <svg
                  class="loss-bar-track"
                  viewBox="0 0 100 8"
                  preserveAspectRatio="none"
                  role="img"
                  :aria-label="row.reason_text + ' ' + row.count + ' 次，占 ' + row.percentage + '%'"
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
              <li
                v-for="(row, index) in topItems"
                :key="row.item_name + '-' + row.unit"
                class="loss-rank-row"
              >
                <span class="loss-rank-no" :class="{ top: index === 0 }">{{ index + 1 }}</span>
                <span class="loss-rank-name">{{ row.item_name }}</span>
                <span class="loss-rank-num">
                  {{ row.count }} 次 · 共 {{ formatQuantity(row.total_quantity) }}{{ row.unit }}
                </span>
              </li>
            </ol>
          </section>

          <section class="loss-block">
            <div class="loss-block-hd">
              <span>日损耗趋势</span>
              <!-- A9：标题右侧直接报峰值是哪天，不用对着柱子找最高那根。 -->
              <span class="loss-hint loss-peak-label">{{ peakLabel || '按当天记了几笔' }}</span>
            </div>
            <p v-if="!hasTrend" class="loss-muted">这个月还没有趋势数据。</p>
            <div v-else class="loss-trend-chart">
              <div class="loss-trend-scale" aria-hidden="true">
                <span
                  v-for="(tick, index) in trendScale"
                  :key="index"
                  class="loss-trend-scale-tick"
                >{{ tick }}</span>
              </div>
              <div class="loss-trend-main">
                <svg
                  class="loss-trend"
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                  role="img"
                  :aria-label="monthLabel + '逐日损耗笔数'"
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
                <div class="loss-trend-axis">
                  <span v-for="bar in dailyBars" :key="bar.date" class="loss-trend-tick">
                    {{ bar.day === 1 || bar.day % 5 === 0 ? bar.day : '' }}
                  </span>
                </div>
              </div>
            </div>
          </section>
        </template>
      </template>

      <section ref="detailSection" class="loss-block loss-detail">
        <div class="loss-block-hd">
          <span>明细</span>
          <span class="loss-detail-meta">
            <span v-if="amendSummary" class="loss-amend">{{ amendSummary }}</span>
            <span class="loss-hint">{{ rangeHint }} · 共 {{ detailTotal }} 笔</span>
          </span>
        </div>
        <!-- A11：统计按一个月、明细按另一段时，把两个口径写在抬头下面。 -->
        <p v-if="detailScopeHint" class="loss-hint loss-scope-hint">{{ detailScopeHint }}</p>
        <p v-if="detailError" class="loss-error loss-detail-error" role="alert">{{ detailError }}</p>

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
            <!-- A7：关键词服务端扩到了物料名 / 备注 / 操作人。 -->
            <input
              v-model="detailKeyword"
              class="loss-input"
              type="search"
              placeholder="搜物料 / 备注 / 人"
              @keyup.enter="search"
            />
            <button class="btn btn-sm" type="button" @click="search">筛</button>
          </div>
        </div>

        <p v-if="detailLoading" class="loss-muted">正在读明细…</p>
        <p v-else-if="!records.length" class="loss-muted">这个范围内没有记录。</p>
        <ul v-else ref="detailList" class="loss-list">
          <li v-for="row in records" :key="row.id" class="loss-item">
            <div class="loss-item-hd">
              <span class="loss-date">{{ row.wastage_date }}</span>
              <b class="loss-name">{{ row.item_name }}</b>
              <span class="loss-qty">{{ formatQuantity(row.quantity) }}{{ row.unit }}</span>
              <span class="loss-reason-tag">{{ row.reason_text }}</span>
              <!-- A4：改过的记录挂一个小角标，鼠标停上去说改了几次。 -->
              <span
                v-if="editCountOf(row)"
                class="loss-edit-flag"
                :title="'改过 ' + editCountOf(row) + ' 次'"
              >改</span>
              <span class="loss-who">{{ row.operator_name }}</span>
            </div>
            <p v-if="row.remark" class="loss-remark">{{ row.remark }}</p>
            <div v-if="row.images && row.images.length" class="loss-thumbs">
              <button
                v-for="shot in row.images"
                :key="shot.capture_id"
                type="button"
                class="loss-thumb"
                aria-label="查看现场照片"
                @click="openLightbox(shot)"
              >
                <img :src="shot.url" alt="现场照片" />
              </button>
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

    <!-- A14：页内灯箱。点遮罩（self）或按 Esc 关闭；图片本身点一下不关。 -->
    <Teleport to="body">
      <div
        v-if="lightboxImage"
        class="loss-lightbox"
        role="dialog"
        aria-modal="true"
        aria-label="现场照片"
        @click.self="closeLightbox"
      >
        <img class="loss-lightbox-img" :src="lightboxImage.url" alt="现场照片" />
        <button
          ref="lightboxCloseBtn"
          class="btn btn-sm loss-lightbox-close"
          type="button"
          @click="closeLightbox"
        >关闭</button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
/* A13：外层吃满可用宽度但封顶 1200、水平居中 —— 内容再宽也不会把页面撑出横向滚动条。 */
.loss-page { width: 100%; max-width: 1200px; margin: 0 auto; padding: 12px 14px 28px; }
.loss-muted { font-size: 13px; color: var(--ink-dim); }
.loss-error { font-size: 13px; color: var(--danger-bright); }
.loss-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; margin-bottom: 14px;
}
.loss-month { display: flex; align-items: center; gap: 8px; }
.loss-month .btn { min-width: 32px; justify-content: center; }
.loss-head-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
/* 页头的按钮都不许折行：它们是一排入口，两行会把页头撑高。 */
.loss-head-actions .btn { white-space: nowrap; }
.loss-updated {
  font-size: 11px; color: var(--ink-dim); white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.loss-register-hint { margin: -6px 0 14px; }

/* A6：统计读失败的错误条。 */
.loss-error-bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 8px 10px; margin-bottom: 14px;
  border: 1px solid var(--danger-soft); border-radius: 8px;
  background: var(--danger-soft);
}
.loss-error-title { font-size: 12.5px; font-weight: 600; color: var(--danger-bright); }
.loss-error-detail {
  flex: 1 1 auto; min-width: 0; font-size: 12px; color: var(--ink-dim);
  word-break: break-word;
}

/* A5：本月没有记录时的空态。 */
.loss-empty {
  border: 1px solid var(--line); border-radius: 10px;
  padding: 22px 14px; margin-bottom: 14px; text-align: center;
  background: var(--surface-2);
}
.loss-empty-title { margin: 0 0 12px; font-size: 14px; color: var(--ink); }
.loss-empty-actions { display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap; }

/* A12：首次加载的骨架（卡 + 图表），加载完就不再出现。 */
.loss-skeleton-cards { display: flex; gap: 10px; margin-bottom: 14px; }
.loss-skeleton {
  border-radius: 10px; background: var(--surface-3);
  animation: loss-skeleton-pulse 1.2s var(--ease) infinite;
}
.loss-skeleton-card { flex: 1 1 0; max-width: 240px; height: 62px; }
.loss-skeleton-chart { height: 168px; margin-bottom: 14px; }
@keyframes loss-skeleton-pulse {
  0%, 100% { opacity: .55; }
  50% { opacity: 1; }
}

/* A8：两张汇总卡并排（不铺满整行，桌面下靠左）。
 *  窄屏必须用 1fr 两等分：minmax(150px, 240px) 的 auto-fit 按 max 算列数，
 *  390 宽只排得下一列 240px，两张卡会竖着叠、右边空出一大条。 */
.loss-cards {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px; margin-bottom: 14px;
}
@media (min-width: 720px) {
  .loss-cards { grid-template-columns: repeat(auto-fit, minmax(150px, 240px)); }
}
.loss-stat {
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 4px;
}
.loss-stat-label { font-size: 11.5px; color: var(--ink-dim); }
.loss-stat-value { font-size: 22px; font-variant-numeric: tabular-nums; }
.loss-stat-value em { font-style: normal; font-size: 12px; color: var(--ink-dim); margin-left: 3px; }
.loss-stat-empty { color: var(--ink-faint); }
.loss-block {
  border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 14px;
}
.loss-block-hd {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  font-size: 13px; letter-spacing: .06em; color: var(--ink); margin-bottom: 10px;
}
.loss-hint { font-size: 11px; letter-spacing: 0; color: var(--ink-dim); }
.loss-top-reason { font-size: 11.5px; letter-spacing: 0; color: var(--ink-dim); }
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

/* A9：趋势图左侧补 y 轴（最大 / 中间 / 0），x 轴留在图下方同一列里对齐。 */
.loss-trend-chart { display: flex; align-items: stretch; gap: 6px; }
.loss-trend-scale {
  display: flex; flex-direction: column; justify-content: space-between;
  flex: none; height: 96px; text-align: right;
  font-size: 9.5px; line-height: 1; color: var(--ink-dim);
  font-variant-numeric: tabular-nums;
}
.loss-trend-main { flex: 1 1 auto; min-width: 0; }
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
.loss-detail-meta { display: flex; align-items: baseline; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
.loss-amend { font-size: 11px; letter-spacing: 0; color: var(--warn); }
.loss-scope-hint { margin: -4px 0 10px; }
.loss-detail-error { margin: 0 0 10px; }
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
/* A4：改过的角标 —— 只在真改过时出现，title 里说次数。 */
.loss-edit-flag {
  font-size: 10.5px; line-height: 1; padding: 2px 5px;
  border: 1px solid var(--line-strong); border-radius: 999px;
  color: var(--ink-dim);
}
.loss-who { font-size: 11.5px; color: var(--ink-dim); margin-left: auto; }
.loss-remark { margin: 4px 0 0; font-size: 12px; color: var(--ink-dim); }
.loss-thumbs { display: flex; gap: 6px; margin-top: 6px; }
.loss-thumb {
  padding: 0; border: 1px solid var(--line); border-radius: 6px;
  background: transparent; cursor: pointer; overflow: hidden; display: block;
  line-height: 0;
}
.loss-thumb:focus-visible { outline: 2px solid var(--action); outline-offset: 2px; }
.loss-thumbs img { width: 46px; height: 46px; object-fit: cover; border-radius: 6px; display: block; }
.loss-pager { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 12px; }

/* A14：页内灯箱。整屏只留这一件事，走队列档。 */
.loss-lightbox {
  position: fixed; inset: 0; z-index: var(--z-queue);
  display: flex; align-items: center; justify-content: center;
  padding: 24px 16px 76px;
  background: var(--wb-scrim-strong);
}
.loss-lightbox-img {
  max-width: min(100%, 960px); max-height: 100%;
  object-fit: contain; border-radius: 8px;
  background: var(--surface-1);
}
.loss-lightbox-close {
  position: absolute; top: 16px; right: 16px;
  min-height: var(--tap); padding: 0 16px;
}
</style>
