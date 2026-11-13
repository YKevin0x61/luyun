<script>
/**
 * 这一页的纯函数（日期 / 摘要口径 / 徽章文案）。
 *
 * 为什么单独放在普通 `<script>`：`<script setup>` 的顶层绑定是 `setup()` 里的局部
 * 变量，导不出去；这些口径要被 `__tests__/StaffMealView*.test.js` 直接单测，
 * 组件与测试必须共用同一份实现，不能在测试里再抄一遍。
 */
export const AUTO_SAVE_DEBOUNCE_MS = 2000

export function pad2(value) {
  return String(value).padStart(2, '0')
}

/** 本机自然日 `YYYY-MM-DD`：员工餐按「哪天吃」算，不走营业日 06:00 口径。 */
export function todayText(now = new Date()) {
  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`
}

export function monthOf(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}`
}

export function currentMonth(now = new Date()) {
  return monthOf(now)
}

/** 「已自动保存 HH:MM」里那个时间。 */
export function clockText(now = new Date()) {
  return `${pad2(now.getHours())}:${pad2(now.getMinutes())}`
}

/** `?date=` 的白名单：只认真实存在的 `YYYY-MM-DD`，其余（缺省 / 脏值 / 数组）当没传。 */
export function parseDateQuery(raw) {
  const value = Array.isArray(raw) ? raw[0] : raw
  if (typeof value !== 'string') return ''
  const text = value.trim()
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return ''
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const date = new Date(year, month - 1, day)
  if (
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day
  ) {
    return ''
  }
  return text
}

/** 月历摘要里给员工看的日期：`2026-11-05` → `11-05`。 */
export function shortDate(text) {
  const match = String(text || '').match(/^\d{4}-(\d{2})-(\d{2})$/)
  return match ? `${match[1]}-${match[2]}` : ''
}

/** S9：导入预览里「已经填过东西的草稿」→ `{ count, dates }`（后端没这个字段时是 0 张）。 */
export function draftWithItemsInfo(preview) {
  const items = preview?.draft_with_items
  const list = Array.isArray(items) ? items : []
  return {
    count: list.length,
    dates: list.map((item) => shortDate(item?.order_date)).filter(Boolean).sort(),
  }
}

/**
 * 员工视角的月历摘要：今天填没填 + 从今天到月底还差几天。
 *
 * 「还差」只数**今天及以后**没提交的天：过去漏掉的天再补也改不了历史，
 * 员工要的是「从现在起还剩几天的量要填」。
 */
export function staffMonthSummary({ days = [], month = '', today = todayText() } = {}) {
  const rows = (Array.isArray(days) ? days : [])
    .map((row) => ({
      date: String(row?.menu_day?.menu_date || row?.order?.order_date || ''),
      submitted: row?.order?.status === 'submitted',
    }))
    .filter((row) => row.date)
  const isCurrentMonth = Boolean(month) && month === today.slice(0, 7)
  const remaining = rows.filter((row) => row.date >= today && !row.submitted).length
  const todayRow = rows.find((row) => row.date === today)
  const todaySubmitted = Boolean(todayRow && todayRow.submitted)
  let text = ''
  if (isCurrentMonth) {
    text = `今天：${todaySubmitted ? '已提交' : '未填'} · 本月还差 ${remaining} 天`
  } else if (!month || month > today.slice(0, 7)) {
    text = `本月还差 ${remaining} 天`
  } else {
    text = '本月已过完'
  }
  return {
    text,
    isCurrentMonth,
    remaining,
    todayStatus: todaySubmitted ? 'submitted' : 'empty',
  }
}

/** 月历里第一张推送失败的单（顶部提示点进去看它）。 */
export function firstFailedDate(days = []) {
  const dates = (Array.isArray(days) ? days : [])
    .filter((row) => row?.order?.push_status === 'failed')
    .map((row) => String(row.order.order_date || row.menu_day?.menu_date || ''))
    .filter(Boolean)
    .sort()
  return dates[0] || ''
}

/** 月历格子的小字：草稿只写「草稿」分不出「没人动过」，填过东西就写「已填 · N 项」。 */
export function cellBadgeText(row) {
  if (!row || !row.order) return ''
  if (row.order.status === 'submitted') {
    return row.order.push_status === 'failed' ? '推送失败' : '已提交'
  }
  const filled = Number(row.order.filled_items || 0)
  return filled > 0 ? `已填 · ${filled} 项` : '草稿'
}
</script>

<script setup>
/**
 * 员工餐采购（ADR 0135 / 2026-11-11 用户裁定）：后勤组的一页，**月历 + 点某天进单**。
 *
 * 两条使用轴：
 *   ① 超级管理员：每月上传一次 .xlsx 菜单（导入即按天建草稿单），临时换菜在这里手改菜单；
 *   ② 员工（勾了「员工餐采购」能力）：逐天点进单，按菜填食材与数量 → 提交即推核心群。
 *
 * 语义（GLOSSARY「员工餐采购」）：
 *   - 采购单按**使用日**一天一张，覆盖当天午 + 晚餐；日期是「哪天吃」，不是下单日；
 *   - 录入按**菜品**、推送按「食材 + 单位」汇总（同食材不同单位分两行）；
 *   - 同名菜只带出食材名（Q9），数量永远留空 —— 当天的量要现场判断；
 *   - 已提交的单还能改（提交者本人 + 超级管理员）：改完点「更新并重新推送」，
 *     标题带「（更新）」、整份清单重发（Q14）；
 *   - 不做作废（Q11）：这天不采购就不填，单据留在草稿即可。
 *
 * 权限由后端判（`api/staff_meal.py` 三 router + `services/staff_meal/service.py`），
 * 这里只按身份决定**显示哪些按钮**：导入 / 手改菜单 / 删草稿 = 超级管理员；
 * 编辑区是否只读用后端给的 `order.can_edit`（与后端 `_require_order_write` 同一判据）。
 *
 * 单日视图是页内状态、不拆子路由（ref 详情见 ADR 0135）：拆了以后「今天」这类入口
 * 与面包屑都要多认一层，而这一页的刷新落点永远是月历。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../../api/client.js'
import { STAFF_MEAL_PATH } from '../../utils/staffMealPaths.js'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_ADMIN } from '../../utils/workbenchIdentity'

const WEEK_NAMES = ['日', '一', '二', '三', '四', '五', '六']
const MEAL_LABELS = { lunch: '午餐', dinner: '晚餐' }
const ROLE_LABELS = { main: '主菜', side: '副菜' }
const PUSH_LABELS = {
  not_pushed: '未推送',
  pushing: '推送中…',
  pushed: '已推送',
  failed: '推送失败',
}
const CONFIRM_RESET_MS = 3000
const NOTICE_RESET_MS = 4000
// 使用日已过（后端 day_expired）：草稿还能存，但提交/推送会被拒 —— 按钮置灰并用这句说明。
const PAST_DAY_HINT = '这天已经过去了：草稿还能改，但不能再提交或推送。'

const identityStore = useWorkbenchIdentityStore()
const isAdmin = computed(() => identityStore.identity === IDENTITY_ADMIN)

// 本机自然日（月历「今天」描边 / 员工摘要「今天填没填」都用它）。
const today = todayText()
// `?date=` 直达（首页「今天」入口）：只在 onMounted 读一次，之后翻月翻日仍走页内状态。
const route = useRoute()

// ── 页面状态 ─────────────────────────────────────────────────────────
const view = ref('month') // month | day
const loading = ref(true)
const errorText = ref('')
const monthValue = ref(currentMonth())
const monthData = ref({ month: '', days: [], summary: {} })
const meta = ref({
  units: [],
  max_items: 300,
  max_item_name: 40,
  max_unit: 10,
  max_quantity: 99999,
  max_remark: 200,
})
const notice = ref('')
const noticeKind = ref('info')
let noticeTimer = 0

const busy = ref(false)

// 自动保存（S8）：草稿态 debounce 2s 静默 PUT；已提交的单永远不进这条路。
const autoSavedAt = ref('')
const autoSaving = ref(false)
let autoSaveTimer = 0
let autoSaveQueued = false
// 最近一次「服务端/本地落定」的编辑签名：watch 用它判断这次触发是真改还是刚回填。
let syncedSignature = ''

// 已提交单「更新并重新推送」的二次确认（S3）：外发到核心群不能一点就走。
const repushConfirm = ref(false)

// 单日视图
const dayDate = ref('')
const dayData = ref(null)
const dishes = ref([])
const others = ref([])
const remark = ref('')
const dayLoading = ref(false)
const sugCache = ref({})
const menuFormOpen = ref(false)
const menuForm = reactive({
  lunch_main: '',
  lunch_side1: '',
  lunch_side2: '',
  dinner_main: '',
  dinner_side1: '',
  dinner_side2: '',
  drink: '',
  freeform: false,
})
const deleteConfirm = ref(false)

// 导入
const fileInput = ref(null)
const importState = ref(null) // { file, preview } | null
const importBusy = ref(false)
// S9：重传会不会动到「已经填过东西」的草稿（后端 preview 里新加的字段）。
const draftWithItems = computed(() => draftWithItemsInfo(importState.value?.preview))

const monthTitle = computed(() => {
  const [year, month] = monthValue.value.split('-')
  return `${year} 年 ${Number(month)} 月`
})
const summary = computed(() => monthData.value.summary || {})
// 后端 `summary.failed` 是并行加的字段：老后端没有这个键时按 0 算（S1）。
const pushFailedCount = computed(() => Number(summary.value.failed ?? 0) || 0)
// 顶部提示点进去要落到「第一张失败单」：按日期取最早那张。
const failedDate = computed(() => firstFailedDate(monthData.value.days || []))
// 员工（非超管）看到的摘要口径：今天填没填 + 本月还差几天（S2）。
const staffSummary = computed(() =>
  staffMonthSummary({
    days: monthData.value.days || [],
    month: monthValue.value,
    today,
  })
)
const order = computed(() => dayData.value?.order || null)
const menuDay = computed(() => dayData.value?.menu_day || null)
const dayEditable = computed(() => {
  if (!order.value) return false
  // 后端详情里带 can_edit（与写接口同一个判据）；老缓存没有时按「有单据即可填」兜底
  // （2026-11-11 起这一域不设能力开关，全员都能填草稿单）。
  if (order.value.can_edit !== undefined) return Boolean(order.value.can_edit)
  return true
})
const isSubmitted = computed(() => order.value?.status === 'submitted')
// 自动保存只服务「还没提交、当前人还能改」的草稿态；已提交的单一个字节都不自动写。
const canAutoSave = computed(
  () =>
    Boolean(dayDate.value) &&
    Boolean(dayData.value) &&
    !isSubmitted.value &&
    dayEditable.value
)
// 本机自然日比较：今天之前的单只能存草稿，提交/重推按钮直接置灰，别让人白点一次。
const isPastDay = computed(
  () => Boolean(dayDate.value) && dayDate.value < today
)
// S10：模板按月生成（30 天的月份、2 月天数不用再手删列），年月从当前查看的月份带给后端。
const templateHref = computed(() => {
  const [year, month] = monthValue.value.split('-')
  return `/api/staff-meal/menu/template?year=${year}&month=${month}`
})
const templateLabel = computed(() => {
  const [year, month] = monthValue.value.split('-')
  return `下载 ${year} 年 ${Number(month)} 月菜单模板`
})
// 提交时间只给人看：库里是带时区的 ISO 串，页面上压成「2026-11-11 19:29」。
const submittedLabel = computed(() => {
  const raw = order.value?.submitted_at
  if (!raw) return ''
  const text = String(raw)
  const match = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return match ? `${match[1]} ${match[2]}` : text
})

const pushLabel = computed(() =>
  order.value ? PUSH_LABELS[order.value.push_status] || '' : ''
)
const dayTitle = computed(() => {
  if (!dayDate.value) return ''
  const [year, month, day] = dayDate.value.split('-').map(Number)
  const weekday = new Date(year, month - 1, day).getDay()
  return `${month} 月 ${day} 日 · 周${WEEK_NAMES[weekday]}`
})
const hasMenu = computed(() => Boolean(menuDay.value))
const lunchText = computed(() =>
  menuText(menuDay.value, 'lunch')
)
const dinnerText = computed(() =>
  menuText(menuDay.value, 'dinner')
)

// ── 月历 ─────────────────────────────────────────────────────────────
const calendarCells = computed(() => {
  const [year, month] = monthValue.value.split('-').map(Number)
  if (!year || !month) return []
  const firstWeekday = new Date(year, month - 1, 1).getDay()
  const daysInMonth = new Date(year, month, 0).getDate()
  const rows = {}
  for (const row of monthData.value.days || []) {
    const date = row.menu_day?.menu_date || row.order?.order_date
    if (date) rows[date] = row
  }
  const cells = []
  for (let i = 0; i < firstWeekday; i += 1) {
    cells.push({ key: `blank-${i}`, day: 0 })
  }
  for (let day = 1; day <= daysInMonth; day += 1) {
    const date = `${monthValue.value}-${pad2(day)}`
    cells.push({
      key: date,
      day,
      date,
      row: rows[date] || null,
      // 今天给格子加描边（S2）：员工打开月历第一眼要能找到今天。
      isToday: date === today,
    })
  }
  return cells
})

function cellBadge(row) {
  return cellBadgeText(row)
}

function cellClass(row) {
  if (!row || !row.order) return ''
  if (row.order.status === 'submitted') {
    return row.order.push_status === 'failed' ? 'is-failed' : 'is-submitted'
  }
  return Number(row.order.filled_items || 0) > 0 ? 'is-filled' : ''
}

function menuText(day, meal) {
  if (!day) return ''
  const fields =
    meal === 'lunch'
      ? [day.lunch_main, day.lunch_side1, day.lunch_side2]
      : [day.dinner_main, day.dinner_side1, day.dinner_side2]
  return fields.filter(Boolean).join(' · ')
}

// ── 数据加载 ─────────────────────────────────────────────────────────
function flash(message, kind = 'info') {
  notice.value = message
  noticeKind.value = kind
  if (noticeTimer) window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => {
    notice.value = ''
  }, NOTICE_RESET_MS)
}

async function loadMeta() {
  try {
    const data = await api.get('/api/staff-meal/meta')
    meta.value = { ...meta.value, ...data }
  } catch (_) {
    // meta 只是上限与单位清单：拿不到也先让页面用默认值动起来。
  }
}

async function loadMonth() {
  loading.value = true
  errorText.value = ''
  try {
    monthData.value = await api.get(`/api/staff-meal/months/${monthValue.value}`)
  } catch (err) {
    errorText.value = err.message || '这个月的员工餐菜单没加载出来'
  } finally {
    loading.value = false
  }
}

function shiftMonth(delta) {
  const [year, month] = monthValue.value.split('-').map(Number)
  const next = new Date(year, month - 1 + delta, 1)
  view.value = 'month'
  monthValue.value = monthOf(next) // watch 会拉新月份
}

async function openDay(date) {
  if (!date) return
  // 换天/换视图前先把挂着的自动保存补上，别把刚敲的改动落在上一张单里。
  flushAutoSaveNow()
  view.value = 'day'
  dayDate.value = date
  dayLoading.value = true
  notice.value = ''
  menuFormOpen.value = false
  deleteConfirm.value = false
  try {
    const data = await api.get(`/api/staff-meal/days/${date}`)
    applyDay(data)
  } catch (err) {
    flash(err.message || '这天的采购单没加载出来', 'error')
    backToMonth()
  } finally {
    dayLoading.value = false
  }
}

/**
 * 当前编辑内容的「签名」：判断 watch 触发的这次变化是不是真改动（applyDay 回填也会触发 watch）。
 * 用 JSON 字符串比逐字段深比简单，数据量就一屏，开销可以忽略。
 */
function editSignature() {
  return JSON.stringify({
    dishes: dishes.value.map((dish) => [
      dish.id,
      dish.rows.map((row) => [row.item_name, row.quantity, row.unit]),
    ]),
    others: others.value.map((row) => [row.item_name, row.quantity, row.unit]),
    remark: remark.value,
  })
}

function applyDay(data) {
  dayData.value = data
  dishes.value = (data.dishes || []).map((dish) => ({
    ...dish,
    rows: (dish.items || []).map(rowFromItem),
  }))
  if (!dishes.value.length) {
    dishes.value = []
  }
  others.value = (data.other_items || []).map(rowFromItem)
  remark.value = data.order?.remark || ''
  // 这一份是服务端的基线：watch 看到签名一致就不会把刚拉回来的内容再存一遍。
  syncedSignature = editSignature()
  autoSavedAt.value = ''
}

function rowFromItem(item) {
  return {
    item_name: item.item_name || '',
    quantity:
      item.quantity === null || item.quantity === undefined
        ? ''
        : String(item.quantity),
    unit: item.unit || '',
  }
}

function blankRow() {
  return { item_name: '', quantity: '', unit: '' }
}

function backToMonth() {
  flushAutoSaveNow()
  view.value = 'month'
  dayDate.value = ''
  dayData.value = null
  autoSavedAt.value = ''
  repushConfirm.value = false
  loadMonth()
}

// ── 编辑行 ───────────────────────────────────────────────────────────
function addRow(target) {
  // 模板里 `others` 已自动解包成数组、`dish` 是对象 —— 两种目标分开判，
  // 别拿 ref 对象做 `===`（模板传进来的是解包后的值）。
  if (Array.isArray(target)) {
    target.push(blankRow())
    return
  }
  target.rows.push(blankRow())
}

function removeRow(rows, index) {
  rows.splice(index, 1)
}

async function suggestForDish(dish) {
  const key = dish.id
  if (sugCache.value[key]) return
  sugCache.value = { ...sugCache.value, [key]: [] }
  try {
    const data = await api.get('/api/staff-meal/suggestions', {
      dish: dish.dish_name,
    })
    sugCache.value = { ...sugCache.value, [key]: data.items || [] }
  } catch (_) {
    // 补全拿不到不阻塞录入。
  }
}

function applySuggestion(dish, suggestion) {
  let row = dish.rows.find((item) => !item.item_name)
  if (!row) {
    row = blankRow()
    dish.rows.push(row)
  }
  row.item_name = suggestion.item_name
  if (!row.unit && suggestion.unit) row.unit = suggestion.unit
}

function collectItems() {
  const items = []
  for (const dish of dishes.value) {
    for (const row of dish.rows) {
      if (!row.item_name.trim() && !String(row.quantity).trim()) continue
      items.push({
        dish_id: dish.id,
        item_name: row.item_name,
        quantity: row.quantity === '' ? null : row.quantity,
        unit: row.unit,
      })
    }
  }
  for (const row of others.value) {
    if (!row.item_name.trim() && !String(row.quantity).trim()) continue
    items.push({
      dish_id: null,
      item_name: row.item_name,
      quantity: row.quantity === '' ? null : row.quantity,
      unit: row.unit,
    })
  }
  return items
}

// ── 保存 / 提交 / 推送 ──────────────────────────────────────────────
async function saveDraft({ silent = false } = {}) {
  if (!dayDate.value) return null
  if (!dayEditable.value) {
    flash('这张单现在不能改：只有提交者本人或超级管理员能改', 'error')
    return null
  }
  // 手动保存/提交前先掐掉挂着的自动保存：同一张单别两条 PUT 打架。
  if (autoSaveTimer) {
    window.clearTimeout(autoSaveTimer)
    autoSaveTimer = 0
  }
  // 静默保存是后台行为：不占 busy、不挡用户继续敲，也不弹「已保存」。
  if (!silent) busy.value = true
  const signature = editSignature()
  const targetDate = dayDate.value
  try {
    const payload = {
      items: collectItems(),
      remark: remark.value,
    }
    // F14 乐观锁：带上服务端给的版本；后端对不上会回 409 + 中文提示。
    if (order.value?.updated_at) {
      payload.expected_updated_at = order.value.updated_at
    }
    const data = await api.put(
      `/api/staff-meal/days/${targetDate}/order`,
      payload
    )
    if (dayDate.value !== targetDate) {
      // 请求期间已经切到别的天了：这份回执不能往回写，免得把新一天的界面盖了。
      return null
    }
    if (editSignature() === signature) {
      applyDay(data)
    } else {
      // 请求期间用户又敲了字：整份回填会盖掉新内容，只刷新单据头，剩下的交给自动保存。
      dayData.value = { ...(dayData.value || {}), order: data.order }
      scheduleAutoSave()
    }
    if (!silent) flash('已保存草稿')
    return data
  } catch (err) {
    const message = err.message || '保存失败，请重试'
    if (dayDate.value !== targetDate) return null
    if (err.status === 409) {
      // F14：这张单刚被别人改过 —— 拉一遍最新内容，别让用户继续在旧数据上改。
      // 先把本地这份认成「已同步」，否则 openDay 的 flush 会立刻拿同一个旧版本号再 PUT 一次（409 循环）。
      syncedSignature = editSignature()
      await openDay(targetDate)
    }
    autoSavedAt.value = ''
    flash(message, 'error')
    return null
  } finally {
    if (!silent) busy.value = false
  }
}

// ── 自动保存（S8）────────────────────────────────────────────────────
function scheduleAutoSave() {
  if (!canAutoSave.value) return
  if (autoSaveTimer) window.clearTimeout(autoSaveTimer)
  autoSaveTimer = window.setTimeout(() => {
    autoSaveTimer = 0
    runAutoSave()
  }, AUTO_SAVE_DEBOUNCE_MS)
}

async function runAutoSave() {
  if (!canAutoSave.value) return
  if (editSignature() === syncedSignature) return
  if (autoSaving.value) {
    // 上一轮还没回来：记一笔，回来后再排一次，保证最后一次改动一定落库。
    autoSaveQueued = true
    return
  }
  autoSaving.value = true
  try {
    const data = await saveDraft({ silent: true })
    if (data) {
      autoSavedAt.value = clockText()
    } else {
      autoSavedAt.value = ''
    }
  } finally {
    autoSaving.value = false
    if (autoSaveQueued) {
      autoSaveQueued = false
      scheduleAutoSave()
    }
  }
}

/** 立刻补存挂着的改动（换天 / 回月历 / 组件卸载时用；请求已发出，不依赖页面还在）。 */
function flushAutoSaveNow() {
  if (autoSaveTimer) {
    window.clearTimeout(autoSaveTimer)
    autoSaveTimer = 0
  }
  if (!canAutoSave.value) return
  if (editSignature() === syncedSignature) return
  runAutoSave()
}

// 草稿态的三种输入都走深度 watch；已提交态 canAutoSave=false，watch 直接不排。
watch(
  [dishes, others, remark],
  () => {
    if (!canAutoSave.value) return
    if (editSignature() === syncedSignature) return
    scheduleAutoSave()
  },
  { deep: true }
)

async function submitOrder() {
  repushConfirm.value = false
  if (!dayDate.value || !dayEditable.value) return
  if (isPastDay.value) {
    flash(PAST_DAY_HINT, 'error')
    return
  }
  const targetDate = dayDate.value
  const saved = await saveDraft({ silent: true })
  if (!saved || dayDate.value !== targetDate) return
  // 本次是「改完重推」还是「第一次提交」：applyDay 之后 isSubmitted 恒为 true，先记下来。
  const wasSubmitted = isSubmitted.value
  busy.value = true
  try {
    const data = await api.post(
      `/api/staff-meal/days/${dayDate.value}/order/submit`
    )
    applyDay(data)
    const status = data.order?.push_status
    if (status === 'failed') {
      flash(data.order?.push_error || '提交成功，但推送没发出去', 'error')
    } else if (status === 'pushing') {
      flash(wasSubmitted ? '已重新排队推送（改完重推会带「（更新）」）' : '提交成功，已排队推送到核心群')
    } else {
      flash(wasSubmitted ? '这张单已推送（改后重推会带「（更新）」）' : '提交成功，已推送到核心群')
    }
  } catch (err) {
    flash(err.message || '提交失败，请重试', 'error')
  } finally {
    busy.value = false
  }
}

/**
 * 吸底主按钮的分派（S3）：已提交的单，重推会往核心群再发一条消息 —— 先确认再发；
 * 首次提交没发出过东西，不拦（原本就没有确认，别给新用户加摩擦）。
 */
function onPrimaryAction() {
  if (isPastDay.value) {
    flash(PAST_DAY_HINT, 'error')
    return
  }
  if (isSubmitted.value) {
    repushConfirm.value = true
    return
  }
  submitOrder()
}

function confirmRepush() {
  repushConfirm.value = false
  submitOrder()
}

function cancelRepush() {
  repushConfirm.value = false
}

async function retryPush() {
  if (!order.value) return
  if (isPastDay.value) {
    flash(PAST_DAY_HINT, 'error')
    return
  }
  busy.value = true
  try {
    const data = await api.post(
      `/api/staff-meal/orders/${order.value.id}/retry-push`
    )
    applyDay(data)
    flash(
      data.order?.push_status === 'failed'
        ? data.order?.push_error || '还是没发出去'
        : '已重新排队推送'
    )
  } catch (err) {
    flash(err.message || '重试推送失败', 'error')
  } finally {
    busy.value = false
  }
}

async function deleteDraft() {
  if (!order.value) return
  if (!deleteConfirm.value) {
    deleteConfirm.value = true
    window.setTimeout(() => {
      deleteConfirm.value = false
    }, CONFIRM_RESET_MS)
    return
  }
  busy.value = true
  try {
    await api.delete(`/api/staff-meal/orders/${order.value.id}`)
    flash('草稿已删除')
    backToMonth()
  } catch (err) {
    flash(err.message || '删除失败', 'error')
  } finally {
    busy.value = false
  }
}

// ── 菜单：超级管理员手改 ────────────────────────────────────────────
function openMenuForm() {
  const day = menuDay.value
  if (!day) return
  for (const field of [
    'lunch_main',
    'lunch_side1',
    'lunch_side2',
    'dinner_main',
    'dinner_side1',
    'dinner_side2',
    'drink',
  ]) {
    menuForm[field] = day[field] || ''
  }
  menuForm.freeform = Boolean(day.freeform)
  menuFormOpen.value = true
}

async function saveMenu() {
  if (!dayDate.value) return
  busy.value = true
  try {
    const payload = { ...menuForm }
    const data = await api.put(
      `/api/staff-meal/menu/days/${dayDate.value}`,
      payload
    )
    applyDay(data)
    menuFormOpen.value = false
    flash('菜单已更新：草稿单的菜品跟着换了')
  } catch (err) {
    flash(err.message || '菜单没保存成功', 'error')
  } finally {
    busy.value = false
  }
}

// ── 导入月度菜单 ────────────────────────────────────────────────────
function pickFile() {
  if (!isAdmin.value) return
  fileInput.value?.click()
}

async function onFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  const [year, month] = monthValue.value.split('-').map(Number)
  importBusy.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    form.append('year', String(year))
    form.append('month', String(month))
    const preview = await api.upload(
      '/api/staff-meal/menu/import/preview',
      form
    )
    importState.value = { file, year, month, preview }
  } catch (err) {
    flash(err.message || '这份表格没解析出来', 'error')
  } finally {
    importBusy.value = false
  }
}

async function confirmImport() {
  const state = importState.value
  if (!state) return
  importBusy.value = true
  try {
    const form = new FormData()
    form.append('file', state.file)
    form.append('year', String(state.year))
    form.append('month', String(state.month))
    form.append(
      'confirm',
      state.preview.existing_orders.length ? 'true' : 'false'
    )
    const result = await api.upload('/api/staff-meal/menu/import', form)
    importState.value = null
    await loadMonth()
    const parts = [`${result.days} 天菜单`]
    if (result.orders_created) parts.push(`新建 ${result.orders_created} 张草稿单`)
    if (result.draft_refreshed) parts.push(`刷新 ${result.draft_refreshed} 张草稿`)
    if (result.submitted_kept) parts.push(`保留 ${result.submitted_kept} 张已提交单`)
    flash(`导入完成：${parts.join('、')}`)
  } catch (err) {
    flash(err.message || '导入失败，请重试', 'error')
  } finally {
    importBusy.value = false
  }
}

function cancelImport() {
  importState.value = null
}

// ── 生命周期 ─────────────────────────────────────────────────────────
watch(monthValue, () => {
  if (view.value === 'month') loadMonth()
})

onMounted(() => {
  document.title = workbenchDocumentTitle('员工餐采购')
  loadMeta()
  // `?date=YYYY-MM-DD` 直达单日（首页「今天」入口）：脏值当没传，回落月历。
  const directDate = parseDateQuery(route?.query?.date)
  if (directDate) {
    // 先切单日、再对齐月份：openDay 同步把 view 置成 day 之后 watch 不会多拉一遍月历。
    openDay(directDate)
    monthValue.value = directDate.slice(0, 7)
    return
  }
  loadMonth()
})

onBeforeUnmount(() => {
  // 组件卸载（关页 / 切路由）时把挂着的改动补存一次，别让最后两秒的输入丢掉。
  flushAutoSaveNow()
  if (noticeTimer) {
    window.clearTimeout(noticeTimer)
    noticeTimer = 0
  }
})
</script>

<template>
  <div class="sm-page">
    <p v-if="errorText" class="sm-banner" role="alert">
      {{ errorText }}
      <button class="sm-link" @click="loadMonth">重试</button>
    </p>

    <!-- S1：推送失败不能只藏在格子里的小字里 —— 顶部给一条可点的提示，点开第一张失败单 -->
    <button
      v-if="view === 'month' && pushFailedCount > 0"
      type="button"
      class="sm-banner sm-banner-action"
      @click="openDay(failedDate)"
    >
      有 {{ pushFailedCount }} 张没推到核心群，点这里看
    </button>

    <!-- 月历：整月概览 + 点某天进单 -->
    <template v-if="view === 'month'">
      <header class="sm-head">
        <button class="sm-nav" aria-label="上个月" @click="shiftMonth(-1)">‹</button>
        <div class="sm-month">
          <h1>{{ monthTitle }}</h1>
          <p class="sm-sub">
            <template v-if="isAdmin">
              本月 {{ summary.days || 0 }} 天 · 已提交 {{ summary.submitted || 0 }} 张
              <template v-if="summary.draft"> · 草稿 {{ summary.draft }} 张</template>
              <template v-if="pushFailedCount"> · 推送失败 {{ pushFailedCount }} 张</template>
            </template>
            <template v-else>
              {{ staffSummary.text }}<template v-if="pushFailedCount"> · 推送失败 {{ pushFailedCount }} 张</template>
            </template>
          </p>
        </div>
        <button class="sm-nav" aria-label="下个月" @click="shiftMonth(1)">›</button>
      </header>

      <div class="sm-toolbar">
        <button
          v-if="isAdmin"
          class="sm-btn sm-btn-soft"
          :disabled="importBusy"
          @click="pickFile"
        >
          {{ importBusy ? '解析中…' : '导入月度菜单' }}
        </button>
        <!-- S10：整月表格按这个模板填，否则列顺序对不上导入会失败；模板按当前月份动态生成 -->
        <a
          v-if="isAdmin"
          class="sm-link sm-template"
          :href="templateHref"
          download
        >
          {{ templateLabel }}
        </a>
        <p class="sm-hint">
          点某天进单；导入一次整月，之后每天由员工填食材与数量。
        </p>
        <input
          ref="fileInput"
          class="sm-file"
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          @change="onFileChange"
        />
      </div>

      <div class="sm-week">
        <span v-for="name in WEEK_NAMES" :key="name">{{ name }}</span>
      </div>
      <div class="sm-grid" :aria-busy="loading">
        <button
          v-for="cell in calendarCells"
          :key="cell.key"
          class="sm-cell"
          :class="[cellClass(cell.row), { 'is-today': cell.isToday }]"
          :disabled="!cell.row"
          @click="cell.row && openDay(cell.date)"
        >
          <span v-if="!cell.day" class="sm-cell-blank" aria-hidden="true" />
          <template v-else>
            <span class="sm-cell-day">{{ cell.day }}</span>
            <span v-if="cell.row?.menu_day?.lunch_main" class="sm-cell-menu">
              {{ cell.row.menu_day.lunch_main }}
            </span>
            <span v-if="cell.row?.order?.submitter_name" class="sm-cell-who">
              {{ cell.row.order.submitter_name }}
            </span>
            <span v-if="cellBadge(cell.row)" class="sm-cell-badge">
              {{ cellBadge(cell.row) }}
            </span>
          </template>
        </button>
      </div>
      <p v-if="!loading && !monthData.days?.length" class="sm-empty">
        这个月还没有菜单。{{ isAdmin ? '点上面的「导入月度菜单」上传整月表格。' : '等超级管理员导入当月菜单后就能填单了。' }}
      </p>
    </template>

    <!-- 单日：菜单 + 逐菜填食材 + 吸底提交 -->
    <template v-else>
      <header class="sm-dayhead">
        <button class="sm-back" @click="backToMonth">‹ 月历</button>
        <div class="sm-month">
          <h1>{{ dayTitle }}</h1>
          <p class="sm-sub">
            <span class="sm-pill" :class="'is-' + (order?.status || 'draft')">
              {{ order ? (isSubmitted ? '已提交' : '草稿') : '无单据' }}
            </span>
            <span v-if="isSubmitted && pushLabel" class="sm-push" :class="'push-' + order.push_status">
              {{ pushLabel }}
            </span>
            <!-- S1：推送失败的原因要能在这里看到，不用回月历猜 -->
            <span v-if="order?.push_error" class="sm-push-error">
              原因：{{ order.push_error }}
            </span>
            <span v-if="isSubmitted && order.submitter_name" class="sm-submitter">
              提交人 {{ order.submitter_name }}
            </span>
          </p>
        </div>
        <button
          v-if="isAdmin && hasMenu"
          class="sm-btn sm-btn-ghost"
          @click="menuFormOpen ? (menuFormOpen = false) : openMenuForm()"
        >
          {{ menuFormOpen ? '收起' : '改菜单' }}
        </button>
      </header>

      <p v-if="notice" class="sm-notice" :class="'is-' + noticeKind" role="status">
        {{ notice }}
      </p>

      <p v-if="dayLoading" class="sm-empty">正在加载这天的菜单…</p>

      <template v-if="!dayLoading && dayData">
        <section v-if="hasMenu" class="sm-card sm-menu-card">
          <h2>当天菜单</h2>
          <dl class="sm-menu-list">
            <div v-if="lunchText">
              <dt>午餐</dt>
              <dd>{{ lunchText }}</dd>
            </div>
            <div v-if="dinnerText">
              <dt>晚餐</dt>
              <dd>{{ dinnerText }}</dd>
            </div>
            <div v-if="menuDay?.drink">
              <dt>饮品/水果</dt>
              <dd>{{ menuDay.drink }}</dd>
            </div>
            <div v-if="menuDay?.freeform" class="sm-freeform">
              这天菜单写了「自主发挥」：按当天实际菜来填，
              菜单里没有的调料 / 临时加的东西记到下面的「其他采购项」。
            </div>
          </dl>
        </section>

        <!-- 超级管理员：手改菜单（导入后临时换菜 / 模板与表格不一致的兜底） -->
        <section v-if="menuFormOpen" class="sm-card">
          <h2>改这天的菜单</h2>
          <div class="sm-menu-form">
            <label v-for="field in [
              ['lunch_main', '午餐主菜'],
              ['lunch_side1', '午餐副菜 1'],
              ['lunch_side2', '午餐副菜 2'],
              ['dinner_main', '晚餐主菜'],
              ['dinner_side1', '晚餐副菜 1'],
              ['dinner_side2', '晚餐副菜 2'],
              ['drink', '饮品/水果'],
            ]" :key="field[0]">
              <span>{{ field[1] }}</span>
              <input v-model="menuForm[field[0]]" class="sm-input" maxlength="80" />
            </label>
            <label class="sm-check">
              <input v-model="menuForm.freeform" type="checkbox" />
              <span>这天是「自主发挥」</span>
            </label>
          </div>
          <div class="sm-form-actions">
            <button class="sm-btn" @click="menuFormOpen = false">取消</button>
            <button class="sm-btn sm-btn-primary" :disabled="busy" @click="saveMenu">
              保存菜单
            </button>
          </div>
          <p class="sm-hint">改菜单会同步到当天的草稿单（已提交的单不动）。</p>
        </section>

        <p v-if="!hasMenu && !order" class="sm-empty">
          {{ dayDate }} 这天没有员工餐菜单。
        </p>

        <template v-if="order">
          <p v-if="!dayEditable" class="sm-banner sm-banner-soft">
            这张单{{ isSubmitted ? '已经提交了' : '现在不能改' }}：只有提交者本人或超级管理员能改。
          </p>

          <section
            v-for="dish in dishes"
            :key="dish.id"
            class="sm-card sm-dish"
          >
            <header class="sm-dish-head">
              <h2>{{ dish.dish_name }}</h2>
              <span class="sm-tag">{{ MEAL_LABELS[dish.meal] || '' }}</span>
              <span v-if="dish.role" class="sm-tag sm-tag-soft">{{ ROLE_LABELS[dish.role] || dish.role }}</span>
            </header>
            <div
              v-for="(row, index) in dish.rows"
              :key="index"
              class="sm-row"
            >
              <input
                v-model="row.item_name"
                class="sm-input sm-name"
                placeholder="食材"
                :disabled="!dayEditable"
                @focus="suggestForDish(dish)"
              />
              <input
                v-model="row.quantity"
                class="sm-input sm-qty"
                type="number"
                inputmode="decimal"
                min="0"
                step="0.5"
                placeholder="数量"
                :disabled="!dayEditable"
              />
              <select
                v-model="row.unit"
                class="sm-input sm-unit"
                :disabled="!dayEditable"
              >
                <option value="">单位</option>
                <option v-for="unit in meta.units" :key="unit" :value="unit">
                  {{ unit }}
                </option>
              </select>
              <button
                v-if="dayEditable"
                class="sm-icon"
                :aria-label="'删掉 ' + dish.dish_name + ' 的这行'"
                @click="removeRow(dish.rows, index)"
              >
                ×
              </button>
            </div>
            <div v-if="dayEditable && sugCache[dish.id]?.length" class="sm-chips">
              <button
                v-for="sug in sugCache[dish.id].slice(0, 6)"
                :key="sug.item_name + ':' + (sug.unit || '')"
                class="sm-chip"
                @click="applySuggestion(dish, sug)"
              >
                {{ sug.item_name }}<em v-if="sug.unit"> · {{ sug.unit }}</em>
              </button>
            </div>
            <button
              v-if="dayEditable"
              class="sm-add"
              @click="addRow(dish)"
            >
              + 加食材
            </button>
          </section>

          <section class="sm-card">
            <header class="sm-dish-head">
              <h2>其他采购项</h2>
              <span class="sm-tag sm-tag-soft">菜单里没有的</span>
            </header>
            <p class="sm-hint">调料、临时加买的东西记在这里，推送时会和其他食材一起汇总。</p>
            <div v-for="(row, index) in others" :key="index" class="sm-row">
              <input
                v-model="row.item_name"
                class="sm-input sm-name"
                placeholder="食材 / 调料"
                :disabled="!dayEditable"
              />
              <input
                v-model="row.quantity"
                class="sm-input sm-qty"
                type="number"
                inputmode="decimal"
                min="0"
                step="0.5"
                placeholder="数量"
                :disabled="!dayEditable"
              />
              <select v-model="row.unit" class="sm-input sm-unit" :disabled="!dayEditable">
                <option value="">单位</option>
                <option v-for="unit in meta.units" :key="unit" :value="unit">
                  {{ unit }}
                </option>
              </select>
              <button
                v-if="dayEditable"
                class="sm-icon"
                aria-label="删掉这一项"
                @click="removeRow(others, index)"
              >
                ×
              </button>
            </div>
            <button v-if="dayEditable" class="sm-add" @click="addRow(others)">
              + 加一项
            </button>
          </section>

          <section class="sm-card">
            <h2>单据备注</h2>
            <textarea
              v-model="remark"
              class="sm-input sm-textarea"
              rows="2"
              :maxlength="meta.max_remark"
              placeholder="给采购的人留一句话（会出现在群消息里）"
              :disabled="!dayEditable"
            />
            <p v-if="isSubmitted && order.submitter_name" class="sm-hint">
              {{ order.submitter_name }} 于 {{ submittedLabel }} 提交
            </p>
          </section>

          <footer class="sm-actions">
            <p class="sm-hint">
              推送按「食材 + 单位」汇总，同食材不同单位分两行；标题带日期，改后重推带「（更新）」。
            </p>
            <!-- S8：草稿自动保存的回执（2s 防抖，只在真存成功后出现） -->
            <p v-if="dayEditable && !isSubmitted && autoSavedAt" class="sm-autosave">
              已自动保存 {{ autoSavedAt }}
            </p>
            <!-- 过去日只能存草稿：按钮置灰，别等点了才被后端 409 拒 -->
            <p v-if="isPastDay" class="sm-hint">{{ PAST_DAY_HINT }}</p>
            <div class="sm-actions-row">
              <button
                v-if="dayEditable"
                class="sm-btn"
                :class="{ 'sm-btn-primary': isSubmitted }"
                :disabled="busy"
                @click="saveDraft()"
              >
                {{ isSubmitted ? '保存修改' : '保存草稿' }}
              </button>
              <button
                v-if="dayEditable"
                class="sm-btn"
                :class="isSubmitted ? 'sm-btn-soft' : 'sm-btn-primary'"
                :disabled="busy || isPastDay"
                @click="onPrimaryAction"
              >
                {{ isSubmitted ? '更新并重新推送' : '提交并推送到核心群' }}
              </button>
              <button
                v-if="dayEditable && order?.push_status === 'failed'"
                class="sm-btn sm-btn-warn"
                :disabled="busy || isPastDay"
                @click="retryPush"
              >
                重试推送
              </button>
            </div>
            <button
              v-if="isAdmin && !isSubmitted"
              class="sm-link sm-danger"
              @click="deleteDraft"
            >
              {{ deleteConfirm ? '再点一次确认删除' : '删除这张草稿单' }}
            </button>
          </footer>
        </template>
      </template>
    </template>

    <!-- 导入确认（覆盖菜单 / 新建草稿的前置说明） -->
    <Teleport to="body">
      <div v-if="importState" class="sm-scrim" @click.self="cancelImport">
        <div class="sm-dialog" role="dialog" aria-modal="true">
          <h2>导入 {{ importState.preview.month }} 的员工餐菜单</h2>
          <p>
            解析出 <strong>{{ importState.preview.days }} 天</strong>菜单
            <template v-if="importState.preview.freeform_days">
              （其中 {{ importState.preview.freeform_days }} 天是「自主发挥」）
            </template>
            。
          </p>
          <p v-if="importState.preview.existing_orders.length">
            这个月已经有
            <strong>{{ importState.preview.existing_orders.length }} 张采购单</strong>：
            导入会<strong>覆盖菜单</strong>；已提交的单保留不动，草稿单的菜品会按新菜单刷新。
          </p>
          <p v-else>这个月还没有采购单：会按菜单给每天建一张草稿单。</p>
          <!-- S9：重传会动到「已经填过东西」的草稿 —— 先让人看见是哪几天 -->
          <p v-if="draftWithItems.count" class="sm-dialog-warn">
            其中 <strong>{{ draftWithItems.count }} 张草稿</strong>已经填过东西（{{ draftWithItems.dates.join('、') }}），
            重传会按新菜单刷新菜品；菜单里删掉的菜，它下面的食材会一起删掉。
          </p>
          <p v-if="importState.preview.submitted_days.length" class="sm-dialog-warn">
            其中 {{ importState.preview.submitted_days.length }} 天已经提交，这些单不会被改动。
          </p>
          <div class="sm-actions-row sm-dialog-actions">
            <button class="sm-btn" :disabled="importBusy" @click="cancelImport">
              取消
            </button>
            <button
              class="sm-btn sm-btn-primary"
              :disabled="importBusy"
              @click="confirmImport"
            >
              {{ importBusy ? '导入中…' : '确认导入' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- S3：「更新并重新推送」会往核心群再发一条，点下去之前先确认 -->
    <Teleport to="body">
      <div v-if="repushConfirm" class="sm-scrim" @click.self="cancelRepush">
        <div class="sm-dialog" role="dialog" aria-modal="true">
          <h2>更新并重新推送？</h2>
          <p>
            会在核心群再发一条<strong>标题带「（更新）」的完整清单</strong>，
            群里会看到两版（旧的不会撤回）。只想改单、不想再发群消息的话，
            取消后点「保存修改」。
          </p>
          <div class="sm-actions-row sm-dialog-actions">
            <button class="sm-btn" :disabled="busy" @click="cancelRepush">
              取消
            </button>
            <button class="sm-btn sm-btn-primary" :disabled="busy" @click="confirmRepush">
              {{ busy ? '推送中…' : '确定，更新并重新推送' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
/* 移动优先：390×844 一屏内看完「这天的菜单 + 单据状态」，吸底条是唯一常驻操作区。 */
.sm-page {
  max-width: 720px;
  margin: 0 auto;
  padding: 10px 12px 28px;
  color: var(--ink);
  font-size: 15px;
  line-height: 1.45;
}
.sm-banner {
  margin: 0 0 10px;
  padding: 9px 12px;
  border-radius: 10px;
  border: 1px solid var(--danger-bright);
  background: var(--wb-danger-a10);
  color: var(--ink);
  font-size: 13px;
}
.sm-banner-soft {
  border-color: var(--line-strong);
  background: var(--surface-1);
}
/* S1：可点的失败提示 —— 外观仍是 banner，但整条是一个按钮 */
.sm-banner-action {
  display: block;
  width: 100%;
  font: inherit;
  font-size: 13px;
  line-height: 1.45;
  text-align: left;
  cursor: pointer;
}
.sm-link {
  margin-left: 6px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--action);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.sm-template {
  margin-left: 0;
  font-size: 13px;
}
.sm-danger {
  color: var(--danger-bright);
  align-self: flex-start;
}
.sm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0 10px;
}
.sm-dayhead {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0 10px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 12px;
}
.sm-nav {
  flex: 0 0 auto;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: var(--surface-1);
  color: var(--ink);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}
.sm-back {
  flex: 0 0 auto;
  min-height: 44px;
  padding: 0 10px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: var(--surface-1);
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
}
.sm-month {
  flex: 1 1 auto;
  min-width: 0;
}
.sm-month h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.sm-sub {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--ink-dim);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.sm-submitter {
  font-size: 12px;
  color: var(--ink-dim);
}
.sm-pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  font-size: 11.5px;
  color: var(--ink-dim);
}
.sm-pill.is-submitted {
  border-color: var(--ok);
  color: var(--ok);
}
.sm-push {
  font-size: 11.5px;
}
.sm-push.push-failed {
  color: var(--danger-bright);
}
.sm-push.push-pushed {
  color: var(--ok);
}
.sm-push-error {
  font-size: 11.5px;
  color: var(--danger-bright);
  overflow-wrap: anywhere;
}
.sm-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.sm-file {
  display: none;
}
.sm-hint {
  margin: 0;
  font-size: 12px;
  color: var(--ink-dim);
}
.sm-autosave {
  margin: 0;
  font-size: 11.5px;
  color: var(--ok);
}
.sm-week {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 4px;
  margin: 6px 0 4px;
  font-size: 11.5px;
  color: var(--ink-faint);
  text-align: center;
}
.sm-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 4px;
}
.sm-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-height: 64px;
  padding: 5px 4px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--surface-1);
  color: var(--ink);
  font-size: 11.5px;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
}
.sm-cell:disabled {
  background: transparent;
  border-color: transparent;
  cursor: default;
}
.sm-cell-blank {
  display: block;
}
.sm-cell.is-filled {
  border-color: var(--line-strong);
  background: var(--surface-2);
}
.sm-cell.is-submitted {
  border-color: var(--ok);
  background: var(--wb-ok-a10);
}
.sm-cell.is-failed {
  border-color: var(--danger-bright);
  background: var(--wb-danger-a10);
}
/* S2：今天用描边标出来 —— 不用填色，已提交/推送失败的底色照样要能看见 */
.sm-cell.is-today {
  outline: 2px solid var(--action);
  outline-offset: -2px;
}
.sm-cell.is-today .sm-cell-day {
  color: var(--action);
}
.sm-cell-day {
  font-size: 13px;
  font-weight: 700;
}
.sm-cell-menu {
  color: var(--ink-dim);
  font-size: 10.5px;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sm-cell-who {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 9px;
  color: var(--ink-faint);
}
.sm-cell-badge {
  margin-top: auto;
  font-size: 10px;
  color: var(--action);
}
.sm-cell.is-submitted .sm-cell-badge {
  color: var(--ok);
}
.sm-cell.is-failed .sm-cell-badge {
  color: var(--danger-bright);
}
.sm-empty {
  margin: 22px 0;
  text-align: center;
  font-size: 13px;
  color: var(--ink-dim);
}
.sm-card {
  margin: 0 0 12px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: var(--surface-1);
}
.sm-card h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
}
.sm-menu-card h2 {
  margin-bottom: 8px;
}
.sm-menu-list {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sm-menu-list div {
  display: flex;
  gap: 8px;
  font-size: 13.5px;
}
.sm-menu-list dt {
  flex: 0 0 64px;
  color: var(--ink-dim);
}
.sm-menu-list dd {
  margin: 0;
  flex: 1 1 auto;
  min-width: 0;
}
.sm-freeform {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--wb-warn-a10);
  color: var(--ink-dim);
  font-size: 12px;
}
.sm-dish-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.sm-tag {
  font-size: 11px;
  color: var(--ink-dim);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0 7px;
}
.sm-tag-soft {
  opacity: 0.72;
}
.sm-row {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(0, 0.8fr) minmax(0, 0.8fr) 30px;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}
.sm-input {
  width: 100%;
  min-height: 44px;
  padding: 8px 9px;
  border-radius: 8px;
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink);
  font-size: 16px;
  font-family: inherit;
}
.sm-input:focus {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.sm-input:disabled {
  opacity: 0.6;
}
.sm-qty {
  text-align: right;
}
.sm-unit {
  padding-right: 2px;
}
.sm-textarea {
  resize: vertical;
  min-height: 60px;
  margin-top: 6px;
}
.sm-icon {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink-dim);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}
.sm-add {
  min-height: 44px;
  margin-top: 2px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px dashed var(--line-strong);
  background: transparent;
  color: var(--action);
  font-size: 13px;
  cursor: pointer;
}
.sm-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 2px 0 8px;
}
.sm-chip {
  min-height: 44px;
  padding: 0 9px;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink);
  font-size: 12px;
  cursor: pointer;
}
.sm-chip em {
  color: var(--ink-dim);
  font-style: normal;
}
.sm-menu-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}
.sm-menu-form label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 12px;
  color: var(--ink-dim);
}
.sm-menu-form .sm-input {
  min-height: 44px;
  font-size: 15px;
}
.sm-check {
  flex-direction: row !important;
  align-items: center;
  gap: 8px !important;
  font-size: 13px !important;
  color: var(--ink) !important;
}
.sm-check input {
  width: 18px;
  height: 18px;
}
.sm-form-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.sm-notice {
  margin: 0 0 10px;
  padding: 9px 12px;
  border-radius: 10px;
  border: 1px solid var(--ok);
  background: var(--wb-ok-a10);
  font-size: 13px;
}
.sm-notice.is-error {
  border-color: var(--danger-bright);
  background: var(--wb-danger-a10);
}
.sm-actions {
  position: sticky;
  bottom: calc(8px + var(--safe-b));
  z-index: var(--z-tabbar);
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 14px;
  padding: 10px 0 calc(6px + var(--safe-b));
  background: var(--surface-0);
  border-top: 1px solid var(--line);
}
.sm-actions-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.sm-btn {
  min-height: 44px;
  padding: 0 14px;
  border-radius: 10px;
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink);
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
}
.sm-qty::-webkit-outer-spin-button,
.sm-qty::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.sm-qty {
  -moz-appearance: textfield;
  appearance: textfield;
}
.sm-btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.sm-btn-primary {
  flex: 1 1 auto;
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 700;
}
.sm-btn-soft {
  border-color: var(--action);
  background: var(--action-soft);
  color: var(--ink);
}
.sm-btn-ghost {
  min-height: 44px;
  padding: 0 10px;
  font-size: 12.5px;
  background: transparent;
}
.sm-btn-warn {
  border-color: var(--warn);
  color: var(--ink);
}
.sm-scrim {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 18px;
  background: var(--scrim);
}
.sm-dialog {
  width: 100%;
  max-width: 420px;
  padding: 16px;
  border-radius: 14px;
  border: 1px solid var(--line-strong);
  background: var(--surface-0);
  color: var(--ink);
  box-shadow: 0 18px 48px var(--scrim);
  font-size: 13.5px;
}
.sm-dialog h2 {
  margin: 0 0 10px;
  font-size: 16px;
}
.sm-dialog p {
  margin: 0 0 8px;
  color: var(--ink-dim);
}
.sm-dialog p strong {
  color: var(--ink);
}
.sm-dialog-warn {
  color: var(--warn) !important;
}
.sm-dialog-actions {
  justify-content: flex-end;
  margin-top: 12px;
}
.sm-dialog-actions .sm-btn-primary {
  flex: 0 0 auto;
}
@media (max-width: 720px) {
  .sm-actions {
    bottom: calc(var(--tab-h, 56px) + var(--safe-b) + 8px);
  }
}
</style>
