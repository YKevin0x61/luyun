<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { hygieneDocumentTitle } from '../../utils/hygieneCopy'

/** 卫生趋势（2026-10-05 用户裁定：超级管理员要能看到趋势）。
 *
 *  现场那七页全是「当下」（今天的队列、当前的待办），数据页是「台账与导出」——
 *  没有一处回答「这一周比上一周好还是差」。这一页就是那个回答：
 *  汇总一行 → 按天一次通过率曲线 → 按工作区 / 按驳回原因两张小表。
 *
 *  **数字一律用服务端给的**：`pass_rate` 的分子分母（一次通过 /（一次通过 + 驳回））由
 *  服务端在同一份事件记录上算好，daily / zones / totals 三处是同一个口径。前端再除一遍
 *  就是第二份口径 —— 「两个页面两个数」是最难查的一类账，红黑榜与本页对不上就属于这种。
 *
 *  `pass_rate` 在「那天一次判定都没有」时是 **`null`，不是 0**：0% 的含义是「判定了、
 *  全被驳回」，null 的含义是「那天没判过」。所以曲线在这里**断开**、表里显示「—」——
 *  把 null 当 0 画，等于把「没验收」说成「全军覆没」，两个完全相反的结论。
 *
 *  `missed`（逾期）是**另一条轴**：它不进通过率的分母（否则「驳得多」和「没人做」会混成
 *  一个数），本页只在顶部汇总与按区表里单独列次数，曲线只画通过率。
 *
 *  **曲线手写 SVG，不引图表库**：这一页只要一条折线，而图表库是几百 KB 的依赖 + 一整套
 *  自己的主题与初始化生命周期，为一条线引进来不值当，也会给工作台这批页面开一个「以后
 *  都这么画」的先例（系统健康面板那批面板是同一种取舍：明确不加载 echarts）。
 */

// 7 / 30 / 84：84 是服务端的事件保留期上限（那边 `min(days, 84)`），再大的窗口拿到的
// 还是这么多天，所以就到 84 为止。默认 30：够看出「这周比上周」，又不至于摊成一团。
const WINDOWS = [7, 30, 84]
const DEFAULT_DAYS = 30

// 标签页标题自己写（`HygieneHomeView` / `ForbiddenView` 那几页同样这么做）：现场壳是拿
// `utils/hygieneCopy.js` 里那份手写的 rail 名单认「现在是哪一页」的，那一份里还没有这页，
// 认不出来就退回第一项 —— 不写这一行，浏览器标签上会挂着「卫生工作区」。
// （rail 名单补上这一页之后，这一行与外壳写的是同一个字符串，可以删。）
document.title = hygieneDocumentTitle('卫生趋势')

const days = ref(DEFAULT_DAYS)
const data = ref(null)
const loading = ref(true)
const errorText = ref('')

// SVG 的坐标系就用**像素**（viewBox 与实测宽度一致，1 单位 = 1px）：文字与圆点都按真实
// 尺寸画，窄屏不会跟着缩。不能用 `preserveAspectRatio="none"` 拉伸 —— 那会把圆点压成
// 椭圆、把轴上那几行 11px 的字也一起压小，而这些字是要给人读的。
const chartHost = ref(null)
const chartWidth = ref(720)
const CHART_HEIGHT = 220
const PAD = { left: 46, right: 16, top: 14, bottom: 28 }

let resizeObserver = null

onMounted(() => {
  observeChartWidth()
  load()
})

onBeforeUnmount(() => {
  if (resizeObserver) resizeObserver.disconnect()
  resizeObserver = null
})

// 不订实时 nudge（现场那几页订，是因为它们显示的是「现在该干什么」）：趋势接口要在服务端
// 扫整个窗口的事件，别人每交一张照片就替全店重算一次不值当。想看新数，按「刷新」。
async function load() {
  loading.value = true
  errorText.value = ''
  try {
    data.value = await api.get('/api/hygiene/admin/trend', { days: days.value })
  } catch (err) {
    // 失败就把数据清掉：留着上一份会在换窗口失败时把 30 天的数字挂在「7 天」的标题底下。
    data.value = null
    errorText.value = err.message || '无法加载卫生趋势'
  } finally {
    loading.value = false
  }
}

async function pickWindow(option) {
  if (option === days.value || loading.value) return
  days.value = option
  // 换窗口先把旧数据清掉：另一个窗口的数字不能顶在这个窗口的标题下。
  data.value = null
  await load()
}

function observeChartWidth() {
  const host = chartHost.value
  if (!host) return
  const measure = () => {
    const width = Math.round(host.clientWidth || 0)
    // 首帧或 jsdom 里宽度可能是 0：别把图缩成 0 宽（那时什么都画不出来），保留默认值。
    if (width > 0) chartWidth.value = width
  }
  measure()
  if (typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(measure)
  resizeObserver.observe(host)
}

// ── 日期算术 ───────────────────────────────────────────────────────────────
// 'YYYY-MM-DD' → 整数天序号。刻意手拆再 `Date.UTC`：`new Date('2026-10-04')` 按 UTC 解析，
// 而 `getDate()` 一类的读数又按本地时区 —— 两者混用会在凌晨差一天。这里只做减法，
// 全程 UTC，跟运行机器的时区无关。窗口的首尾**只用服务端给的** `since` / `business_date`，
// 营业日 06:00 切日那条口径在服务端，前端不自己算「今天」。
function dayNumber(iso) {
  const [year, month, day] = String(iso || '').split('-').map(Number)
  if (!year || !month || !day) return null
  return Math.floor(Date.UTC(year, month - 1, day) / 86400000)
}

function isoOfDay(number) {
  return new Date(number * 86400000).toISOString().slice(0, 10)
}

// 通过率的**唯一**显示口径：null（没判定）显示「—」，绝不当 0%。
function formatRate(rate) {
  return typeof rate === 'number' && Number.isFinite(rate) ? `${(rate * 100).toFixed(1)}%` : '—'
}

// ── 数据切面 ───────────────────────────────────────────────────────────────
const daily = computed(() => data.value?.daily || [])
const totals = computed(() => data.value?.totals || {
  first_pass: 0, rejected: 0, captured: 0, missed: 0, pass_rate: null,
})
const hasEvents = computed(() => daily.value.length > 0)
const sinceDay = computed(() => dayNumber(data.value?.since))

const windowLabel = computed(() => {
  const payload = data.value
  if (!payload) return ''
  return `窗口 ${payload.since} ~ ${payload.business_date} · ${payload.days} 天 · 按营业日切日`
})

// 按区：驳回降序，店长一眼看出哪个区老出问题。服务端已经这么排了（并列时按逾期、再按
// zone_id），这里再排一次只是不让展示顺序依赖接口实现；JS 的 sort 是稳定的，并列的相对
// 次序照旧。
const zoneRows = computed(() => (
  [...(data.value?.zones || [])].sort((a, b) => (b.rejected || 0) - (a.rejected || 0))
))

// 驳回原因 TOP：一条一行，外加一条按最高次数归一的比例条（一眼看出哪条占大头）。
const reasonRows = computed(() => {
  const rows = data.value?.reasons || []
  const top = rows.reduce((max, row) => Math.max(max, row.count || 0), 0) || 1
  return rows.map((row) => ({
    ...row,
    width: `${Math.round(((row.count || 0) / top) * 100)}%`,
  }))
})

// ── 曲线几何 ───────────────────────────────────────────────────────────────
const plot = computed(() => {
  const innerW = Math.max(80, chartWidth.value - PAD.left - PAD.right)
  const innerH = CHART_HEIGHT - PAD.top - PAD.bottom
  // 横轴按**日期**排，不按数组下标：daily 只包含有事件的日子，缺的那天必须留出空白 ——
  // 按下标画会把「隔了 20 天的两天」贴在一起，看着像连着两天。
  const span = Math.max(1, Number(data.value?.days || days.value) - 1)
  const since = sinceDay.value
  return {
    innerW,
    innerH,
    span,
    xOf: (day) => PAD.left + ((day - since) / span) * innerW,
    yOf: (rate) => PAD.top + (1 - rate) * innerH,
  }
})

const points = computed(() => {
  const since = sinceDay.value
  if (since === null) return []
  const list = []
  for (const row of daily.value) {
    const day = dayNumber(row.business_date)
    const rate = row.pass_rate
    // null 的日子不进点集：它没有通过率可画（见文件头那段）。
    if (day === null || typeof rate !== 'number' || !Number.isFinite(rate)) continue
    list.push({ day, date: row.business_date, rate, row, x: plot.value.xOf(day), y: plot.value.yOf(rate) })
  }
  return list
})

/** 折线按连续的日子分段：中间隔了没判定的日子就断开。不插值、不跨越 —— 连起来等于说
 *  「这两天之间通过率在慢慢变」，而事实是那天根本没判过。 */
const segments = computed(() => {
  const result = []
  let current = []
  for (const point of points.value) {
    const prev = current[current.length - 1]
    if (prev && point.day - prev.day !== 1) {
      result.push(current)
      current = []
    }
    current.push(point)
  }
  if (current.length) result.push(current)
  return result
})

function segmentPath(segment) {
  return segment
    .map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
    .join(' ')
}

/** 纵轴固定 0%–100%（不按当窗数据缩放）：换窗口时同一条线的坡度含义不变，也不会把
 *  「全在 80%~90% 之间」放大成剧烈波动。0 / 50 / 100 三条参照线都要标出来。 */
const gridLines = computed(() => [1, 0.5, 0].map((rate) => ({
  rate,
  y: plot.value.yOf(rate),
  label: `${rate * 100}%`,
})))

// 点少时画得大一点；84 天在手机上每个点之间只有几个像素，点大了会连成一条粗线。
const dotRadius = computed(() => (Number(data.value?.days || days.value) <= 31 ? 3 : 1.8))

const xTicks = computed(() => {
  const since = sinceDay.value
  if (since === null) return []
  // 窄屏少放几个日期：首 / 中 / 末三个比五个挤在一起好读。
  const count = chartWidth.value < 520 ? 3 : 5
  const offsets = new Set()
  for (let index = 0; index < count; index += 1) {
    offsets.add(Math.round((plot.value.span * index) / (count - 1)))
  }
  const all = [...offsets]
  return all.map((offset, index) => ({
    x: plot.value.xOf(since + offset),
    label: isoOfDay(since + offset).slice(5),
    anchor: index === 0 ? 'start' : (index === all.length - 1 ? 'end' : 'middle'),
  }))
})

const chartLabel = computed(() => {
  const rates = points.value.map((point) => point.rate)
  if (!rates.length) return '按天一次通过率曲线：窗口内没有判定，无法绘制'
  return `按天一次通过率曲线：${data.value?.since} 至 ${data.value?.business_date}，`
    + `${rates.length} 天有判定，最高 ${formatRate(Math.max(...rates))}，最低 ${formatRate(Math.min(...rates))}`
})

/** 最近 7 天 vs 前 7 天 —— 这一页存在的理由就是回答「这周比上周好还是差」。
 *
 *  口径与 totals 完全一致：**把两段里每天的计数相加再相除**，不是把每天的 pass_rate 求
 *  平均 —— 求平均等于给「一天 1 次判定」和「一天 50 次判定」同样的权重，那是另一份口径。
 *  任一段没有判定（分母为 0）就不显示这一行：没有数可比，也不能拿 0% 冒充。
 *  窗口不足两周时不显示。 */
const weekCompare = computed(() => {
  const since = sinceDay.value
  const span = Number(data.value?.days || 0)
  if (since === null || span < 14) return null
  const end = since + span - 1
  const recent = tallyRange(end - 6, end)
  const previous = tallyRange(end - 13, end - 7)
  if (recent.rate === null || previous.rate === null) return null
  const delta = (recent.rate - previous.rate) * 100
  return {
    recentText: formatRate(recent.rate),
    previousText: formatRate(previous.rate),
    deltaText: Math.abs(delta) < 0.05 ? '持平' : `${delta > 0 ? '▲' : '▼'} ${Math.abs(delta).toFixed(1)}pt`,
    better: delta > 0,
  }
})

function tallyRange(fromDay, toDay) {
  const sum = { first_pass: 0, rejected: 0 }
  for (const row of daily.value) {
    const day = dayNumber(row.business_date)
    if (day === null || day < fromDay || day > toDay) continue
    sum.first_pass += row.first_pass || 0
    sum.rejected += row.rejected || 0
  }
  const judged = sum.first_pass + sum.rejected
  return { ...sum, judged, rate: judged ? sum.first_pass / judged : null }
}

/** 每个点的悬停说明：把那天四个数都摆出来，逾期也在里面（它不参与上面的率，但那天到底
 *  有多少项逾期是解释曲线的一个线索）。 */
function pointTitle(point) {
  const row = point.row
  return `${point.date} · 一次通过率 ${formatRate(point.rate)}`
    + `（一次通过 ${row.first_pass} · 驳回 ${row.rejected} · 实拍 ${row.captured} · 逾期 ${row.missed}）`
}
</script>

<template>
  <div class="trend-page">
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Trend · 卫生趋势</p>
        <h1>卫生趋势</h1>
        <p>按营业日回看窗口内的一次通过率、驳回与逾期：这一周比上一周好还是差。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>
            一次通过率 = 一次通过 /（一次通过 + 驳回），分子分母都由服务端按同一份事件记录
            算好，与红黑榜、按区表同一个口径；本页不另算。
          </p>
          <p>
            逾期是**另一条轴**：它不参与通过率，只看次数（顶部汇总一次、按工作区各一列）。
            图上的曲线只画通过率。
          </p>
          <p>
            某一天或某个工作区一次判定都没有时，通过率是「—」而不是 0%：曲线在那里断开，
            表格里留空 —— 没验收和全被驳回是两回事。
          </p>
          <p>
            事件只保留 84 天，所以窗口最大到 84 天；窗口里没事件的日子不会出现在数据里，
            曲线也就跟着断开。
          </p>
        </details>
      </div>
      <div class="roster-head-actions">
        <div class="trend-range" role="group" aria-label="回看窗口">
          <button
            v-for="option in WINDOWS"
            :key="option"
            type="button"
            class="btn"
            :class="{ 'btn-primary': option === days }"
            :aria-pressed="option === days"
            :disabled="loading"
            @click="pickWindow(option)"
          >
            {{ option }} 天
          </button>
        </div>
        <button type="button" class="btn" :disabled="loading" @click="load">刷新</button>
      </div>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>

    <div class="card trend-summary">
      <div class="trend-headline">
        <strong>{{ formatRate(totals.pass_rate) }}</strong>
        <span>一次通过率</span>
      </div>
      <ul class="trend-figures">
        <li>
          <strong>{{ totals.first_pass }}</strong>
          <span>一次通过（项）</span>
        </li>
        <li>
          <strong>{{ totals.rejected }}</strong>
          <span>被驳回（次）</span>
        </li>
        <li class="is-missed">
          <strong>{{ totals.missed }}</strong>
          <span>逾期（次）· 另一条轴</span>
        </li>
      </ul>
      <!-- 窗口首尾与天数都用服务端回的（营业日在服务端 06:00 切，前端不自己算「今天」）。 -->
      <p v-if="windowLabel" class="trend-window">{{ windowLabel }}</p>
      <p v-if="weekCompare" class="trend-wow" :class="{ 'is-better': weekCompare.better }" role="status">
        最近 7 天 {{ weekCompare.recentText }} · 前 7 天 {{ weekCompare.previousText }}
        <em>（{{ weekCompare.deltaText }}，两段各自把计数相加再相除）</em>
      </p>
    </div>

    <div class="card trend-card">
      <div class="table-card-header">
        <h3>按天一次通过率 <span>{{ points.length }}</span></h3>
        <p class="trend-note">纵轴固定 0%–100%；没有判定的那天断开，不插值。</p>
      </div>
      <div class="trend-chart">
        <div ref="chartHost" class="trend-plot">
          <div v-if="loading" class="roster-empty">正在加载…</div>
          <div v-else-if="!hasEvents" class="roster-empty">
            这 {{ data?.days || days }} 天里一条验收事件都没有：没有判定就没有通过率，所以不画图。
            换一个窗口看看，或者等今天的验收开始。
          </div>
          <svg
            v-else
            :viewBox="`0 0 ${chartWidth} ${CHART_HEIGHT}`"
            :height="CHART_HEIGHT"
            role="img"
            :aria-label="chartLabel"
          >
            <!-- 参照线：0% / 50% / 100%（50% 用虚线，与首尾两条分开） -->
            <g v-for="line in gridLines" :key="line.label">
              <line
                class="trend-grid"
                :class="{ 'is-mid': line.rate === 0.5 }"
                :x1="PAD.left"
                :x2="chartWidth - PAD.right"
                :y1="line.y"
                :y2="line.y"
              />
              <text class="trend-axis" :x="PAD.left - 8" :y="line.y + 4" text-anchor="end">
                {{ line.label }}
              </text>
            </g>
            <!-- 横轴只标几个日期（全部标出来在手机上会叠成一团） -->
            <text
              v-for="tick in xTicks"
              :key="`tick-${tick.x}-${tick.label}`"
              class="trend-axis"
              :x="tick.x"
              :y="CHART_HEIGHT - 8"
              :text-anchor="tick.anchor"
            >
              {{ tick.label }}
            </text>
            <!-- 折线：一段 = 连续有判定的日子，中间断掉的那几天就是不画 -->
            <path
              v-for="(segment, index) in segments"
              :key="`seg-${index}`"
              class="trend-line"
              :d="segmentPath(segment)"
            />
            <!-- 点仍然每个都画：只有一天有判定的那种「孤点」在折线里是画不出来的 -->
            <circle
              v-for="point in points"
              :key="point.date"
              class="trend-dot"
              :cx="point.x"
              :cy="point.y"
              :r="dotRadius"
            >
              <title>{{ pointTitle(point) }}</title>
            </circle>
          </svg>
        </div>
      </div>
    </div>

    <div class="trend-tables">
      <div class="table-card">
        <div class="table-card-header">
          <h3>按工作区 <span>{{ zoneRows.length }}</span></h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="!zoneRows.length" class="roster-empty">这一段没有工作区的判定记录。</div>
        <div v-else class="data-table-wrap luyun-scrollbar">
          <table class="data-table trend-table">
            <thead>
              <tr>
                <th>工作区</th>
                <th>一次通过</th>
                <th>驳回</th>
                <th>逾期</th>
                <th>一次通过率</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in zoneRows" :key="row.zone_id">
                <td>{{ row.zone_name }}</td>
                <td class="trend-num">{{ row.first_pass }}</td>
                <td class="trend-num">{{ row.rejected }}</td>
                <td class="trend-num">{{ row.missed }}</td>
                <!-- 没有判定的区显示「—」：不是 0% -->
                <td class="trend-num">{{ formatRate(row.pass_rate) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="trend-note trend-note-block">按驳回降序 —— 老出问题的排在最上面。</p>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>驳回原因 TOP <span>{{ reasonRows.length }}</span></h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="!reasonRows.length" class="roster-empty">这一段没有被驳回的记录。</div>
        <ul v-else class="reason-list">
          <li v-for="row in reasonRows" :key="row.reason">
            <span class="reason-text">{{ row.reason }}</span>
            <span class="reason-bar" aria-hidden="true"><i :style="{ width: row.width }" /></span>
            <span class="reason-count">{{ row.count }}</span>
          </li>
        </ul>
        <p class="trend-note trend-note-block">尺度问题出在哪，看这一列。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 页面骨架：与别页同形（宽度上限 + 竖向 14px 间距 + 依次入场），但共享表
   `public/hygiene-admin.css` 那份是别的页面的底盘（两份副本要同步，不放这页的类名进去），
   所以骨架在这页自己写一份，用的还是同一套 `--hy-*` 令牌。 */
.trend-page {
  width: 100%;
  max-width: 80rem;
  margin-inline: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ── 顶部汇总 ─────────────────────────────────────────────── */
.trend-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 26px;
}
.trend-headline {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.trend-headline strong {
  font-family: var(--font-mono);
  font-size: 32px;
  font-weight: 700;
  color: var(--hy-mint);
  font-variant-numeric: tabular-nums;
}
.trend-headline span {
  color: var(--hy-muted);
  font-size: .82rem;
}
.trend-figures {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 22px;
}
.trend-figures li {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.trend-figures strong {
  font-size: 19px;
  font-variant-numeric: tabular-nums;
}
.trend-figures span {
  color: var(--hy-muted);
  font-size: .78rem;
}
/* 逾期是另一条轴：给它自己的琥珀色，免得被读成通过率的一部分。 */
.trend-figures .is-missed strong { color: var(--hy-amber); }
.trend-window {
  flex: 0 0 100%;
  margin: 0;
  color: var(--hy-faint);
  font-size: .74rem;
  font-variant-numeric: tabular-nums;
}
.trend-wow {
  flex: 0 0 100%;
  margin: 0;
  color: var(--hy-muted);
  font-size: .8rem;
  font-variant-numeric: tabular-nums;
}
.trend-wow em {
  font-style: normal;
  color: var(--hy-faint);
  font-size: .74rem;
}
.trend-wow.is-better { color: var(--hy-jade); }

/* ── 曲线 ─────────────────────────────────────────────────── */
.trend-note {
  margin: .35rem 0 0;
  color: var(--hy-faint);
  font-size: .74rem;
}
.trend-note-block {
  margin: 0;
  padding: .6rem 1.1rem .8rem;
}
.trend-chart {
  padding: .5rem .75rem .25rem;
}
/* 量宽度量的是这一个：外层的内边距不能算进去，否则 SVG 的坐标系比实际渲染宽一点，
   整张图（连同轴上的字）会被等比缩小几个百分点 —— 差得不多，但没必要差。 */
.trend-plot {
  padding: 0;
}
.trend-plot svg {
  display: block;
  width: 100%;
  height: auto;
}
/* 网格与参照线 */
.trend-grid {
  stroke: var(--hy-line);
  stroke-width: 1;
}
.trend-grid.is-mid {
  stroke: var(--hy-line-strong);
  stroke-dasharray: 4 5;
}
.trend-axis {
  fill: var(--hy-faint);
  font-family: var(--font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.trend-line {
  fill: none;
  stroke: var(--hy-mint);
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.trend-dot {
  fill: var(--hy-mint-bright);
  stroke: var(--hy-night);
  stroke-width: 1;
}

/* ── 两张小表 ─────────────────────────────────────────────── */
.trend-tables {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}
/* 共享表给数据表表头配了「可点排序」的手型（数据管理那页要用），这两张表不排序 ——
   两个类比全局那条 `.hygiene-admin table.data-table th` 多一个，才压得住它。 */
.data-table.trend-table th {
  cursor: default;
}
.trend-num { font-variant-numeric: tabular-nums; }
.reason-list {
  list-style: none;
  margin: 0;
  padding: .6rem 1.1rem .8rem;
  display: flex;
  flex-direction: column;
  gap: .45rem;
}
.reason-list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 96px auto;
  gap: .6rem;
  align-items: center;
  font-size: .82rem;
}
.reason-text {
  overflow-wrap: anywhere;
}
.reason-bar {
  height: 4px;
  border-radius: 999px;
  background: var(--hy-seal-soft);
  overflow: hidden;
}
.reason-bar i {
  display: block;
  height: 100%;
  background: var(--hy-seal-bright);
}
.reason-count {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--hy-muted);
}
@media (max-width: 900px) {
  .trend-tables { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 520px) {
  /* 窄屏让出比例条的位置给原因文本：一行里三列会挤成一团。 */
  .reason-list li { grid-template-columns: minmax(0, 1fr) auto; }
  .reason-bar { display: none; }
}
</style>
