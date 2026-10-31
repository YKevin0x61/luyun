<script setup>
/**
 * 员工手机端的「加班与补钟」页（票 01）。
 *
 * 一页回答三件事：这个月加了多少、我提过哪些、哪些还没落定。加班记正数、补钟记负数，
 * 走的是**同一张台账** —— 所以表单上没有「选加班还是补钟」那一栏：符号就是类型，
 * `+` / `−` 两个按钮一直按过去就跨过零点了（0 不是一笔登记，`stepHalfHours` 跳过它）。
 *
 * 数据只有一个来源：`GET /api/overtime/me`（员工那个 cookie）。**接口上没有
 * `employee_id`**：员工读得到的、动得了的只有自己那几笔，金额与底薪都不在这条链路上
 * （`docs/adr/0098` 那条边界，票 01 的验收 5）。
 *
 * 三条口径写在页脚里，免得员工自己猜：
 *   · 只能登记**今天与昨天**（自然日零点切，不是营业日）—— 更早的请找店长补录，那是
 *     管理端的逃生口，不是员工能自己开的口子（`docs/adr/0100`）；
 *   · 1 格 = 0.5 小时，单笔上限 ±12 小时；
 *   · 提交后是「待审批」，只有「已批准」那一行算数（票 02 才有人点头）。
 *
 * 反馈走页内一句话（不是 toast），跟「今天」页那张申请卡一个写法。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import { useNudgePull } from '../../composables/useNudgePull'
import { loginRedirectTarget } from '../../utils/loginNext'
import { staffRequest } from '../../utils/hygieneStaff'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { formatDays } from '../../utils/attendanceSheet'
import {
  HALF_HOURS_MAX,
  MAX_REASON,
  MAX_REJECT_REASON,
  entryDayOptions,
  entryStatusText,
  entryStatusTone,
  formatHalfHours,
  stepHalfHours,
  submittedByText,
} from '../../utils/overtimeEntry'

const router = useRouter()
const state = ref('loading') // loading | ready | error
const employee = ref(null)
const today = ref('')
const yesterday = ref('')
const entries = ref([])
const summary = ref(null)
const errorText = ref('')
// 补钟用存假抵扣（ADR 0124）：`/api/overtime/me` 把「我的抵扣单」与「本月还能抵几天」
// 一起带下来 —— 可抵天数在服务端算一份，前端不重复推那套上限。
const deductions = ref([])
const deductionCapacity = ref(null)
const applyOpen = ref(false)
const applyDays = ref(1)
const applyBusy = ref(false)
const applyError = ref('')
const cancelDeductionId = ref(null)

// 上限从接口响应里来（`max_reason` / `max_half_hours`）：页面不写死第二份 50 与 12，
// 服务端改口径时这里跟着走（同「今天」页读 `max_request_note` 的写法）。
const maxReason = ref(MAX_REASON)
const maxHalfHours = ref(HALF_HOURS_MAX)

// 表单：默认 `+0.5` 小时（最常见的那一笔），日期默认今天。
const day = ref('')
const halfHours = ref(1)
const reason = ref('')
const busy = ref(false)
const formError = ref('')
const note = ref('')
const cancelBusyId = ref(null)

// 票 04：店长那一面（第 11 项能力键 `overtime`）。`can_review` 由**服务端**算好带下来，
// 页面不自己重写一遍 caps 判据 —— 权限判据只有一处，改口径时不会漏掉这一页。
const canReview = ref(false)
const pending = ref([])
// 抵扣单跟加班登记同一个待办队列（ADR 0124）：店长一次看完、一次处理完。
const pendingDeductions = ref([])
const rejectingDeductionId = ref(null)
const roster = ref([])
const reviewError = ref('')
const reviewBusyId = ref(null)
const rejectingId = ref(null)
const rejectReason = ref('')
const maxRejectReason = ref(MAX_REJECT_REASON)
// 代录：「替谁记」。空串 = 我自己（走员工那条路），否则是别人的员工号。
const backfillFor = ref('')

const dayOptions = computed(() =>
  entryDayOptions({ today: today.value, yesterday: yesterday.value })
)
const hoursText = computed(() => formatHalfHours(halfHours.value))
const canSubmit = computed(() => !!day.value && !!reason.value.trim() && !busy.value)

// 存假抵扣（ADR 0124）：8.5 小时 = 1 天，只能整天 —— 与加班费公式、考勤表同一个分母。
const HOURS_PER_LEAVE_DAY = 8.5
const capacity = computed(() => deductionCapacity.value || null)
const applyPreview = computed(() => {
  const left = Number(capacity.value?.leave_balance || 0) - applyDays.value
  return `抵 ${applyDays.value} 天后，存假还剩 ${formatDays(left)}；这 ${applyDays.value * HOURS_PER_LEAVE_DAY} 小时补钟不再算净时长。`
})
const capacityText = computed(() => {
  const c = capacity.value
  if (!c) return ''
  return `本月已批补钟 ${formatHalfHours(c.makeup_half_hours)} 小时，已用存假抵 ${c.deducted_days} 天；当前存假 ${formatDays(c.leave_balance)}。`
})
const deductionEmptyText = computed(() => {
  const c = capacity.value
  if (!c) return ''
  const left = Number(c.available_hours || 0)
  if (left <= 0) return '本月没有可抵的补钟。'
  const rest = Math.round(left * 2) % 17
  if (rest === 0) return ''
  return `本月补钟还差 ${((17 - rest) / 2).toFixed(1)} 小时才满一天（8.5 小时）。`
})
function deductionText(item) {
  return `抵 ${item.hours} 小时补钟`
}
function openApply() {
  applyDays.value = Math.max(1, Number(capacity.value?.max_days || 1))
  applyOpen.value = true
  applyError.value = ''
}

async function load(quiet = false) {
  if (!quiet) state.value = 'loading'
  try {
    const data = await staffRequest('/api/overtime/me')
    employee.value = data.employee
    today.value = data.today
    yesterday.value = data.yesterday
    entries.value = data.entries || []
    summary.value = data.summary || null
    deductions.value = data.deductions || []
    deductionCapacity.value = data.deduction_capacity || null
    maxReason.value = data.max_reason || MAX_REASON
    maxHalfHours.value = data.max_half_hours || HALF_HOURS_MAX
    maxRejectReason.value = data.max_reject_reason || MAX_REJECT_REASON
    canReview.value = !!data.can_review
    if (!day.value) day.value = data.today
    state.value = 'ready'
    // 店长那一面单独拉一次（他自己那份记录上面已经拿到了）：它读失败不该把整页
    // 打回错误态 —— 登记与「我的记录」照旧能用。
    if (canReview.value) await loadReview()
  } catch (err) {
    if (err.status === 401) {
      // 会话没了：回员工登录，回来还是这一页（跟「今天」页同一个走法）。
      const target = loginRedirectTarget(router.currentRoute.value)
      if (target) router.replace(target)
      return
    }
    errorText.value = err.message || '读不到你的加班记录'
    state.value = 'error'
  }
}

async function submit() {
  if (busy.value) return
  busy.value = true
  formError.value = ''
  note.value = ''
  // 店长替别人记时走店长那一面（带 `employee_id`），记自己的走原来那条路 —— 两条路
  // 在服务端落到同一个 `submit()`：窗口按**提交人**判，所以店长给自己补很久以前的
  // 一笔也放得开（`docs/adr/0100` / `0103`）。
  const target = canReview.value && backfillFor.value ? Number(backfillFor.value) : null
  const body = {
    entry_date: day.value,
    half_hours: halfHours.value,
    reason: reason.value.trim(),
  }
  if (target) body.employee_id = target
  try {
    await staffRequest(target ? '/api/overtime/review/entries' : '/api/overtime/me', {
      method: 'POST',
      body,
    })
    note.value = target ? '替他记上了，等审批' : '提上去了，等店长批'
    reason.value = ''
    halfHours.value = 1
    await load(true)
  } catch (err) {
    if (err.status === 401) {
      const target = loginRedirectTarget(router.currentRoute.value)
      if (target) router.replace(target)
      return
    }
    // 服务端那句中文就是给员工看的（「更早的请找店长补录」这种），原样显示。
    formError.value = err.message || '没提交成'
  } finally {
    busy.value = false
  }
}

async function cancelEntry(entry) {
  if (cancelBusyId.value) return
  cancelBusyId.value = entry.id
  formError.value = ''
  note.value = ''
  try {
    await staffRequest(`/api/overtime/me/${entry.id}`, { method: 'DELETE' })
    note.value = '那条撤回了'
    await load(true)
  } catch (err) {
    if (err.status === 401) {
      const target = loginRedirectTarget(router.currentRoute.value)
      if (target) router.replace(target)
      return
    }
    formError.value = err.message || '没撤回成'
  } finally {
    cancelBusyId.value = null
  }
}

// ── 存假抵扣（ADR 0124）────────────────────────────────────────────────────

/** 提交一张抵扣单：归月 = 补钟登记月（就是这一页正在看的那个月），单位是整天。 */
async function applyDeduction() {
  if (applyBusy.value) return
  applyBusy.value = true
  applyError.value = ''
  note.value = ''
  try {
    await staffRequest('/api/overtime/me/deductions', {
      method: 'POST',
      body: { month: summary.value?.month, days: applyDays.value },
    })
    note.value = '抵扣申请提上去了，等店长批'
    applyOpen.value = false
    await load(true)
  } catch (err) {
    if (err.status === 401) {
      const target = loginRedirectTarget(router.currentRoute.value)
      if (target) router.replace(target)
      return
    }
    applyError.value = err.message || '没提交成'
  } finally {
    applyBusy.value = false
  }
}

async function cancelDeduction(deduction) {
  if (cancelDeductionId.value) return
  cancelDeductionId.value = deduction.id
  note.value = ''
  applyError.value = ''
  try {
    await staffRequest(`/api/overtime/me/deductions/${deduction.id}`, { method: 'DELETE' })
    note.value = '那张抵扣撤回了'
    await load(true)
  } catch (err) {
    if (err.status === 401) {
      const target = loginRedirectTarget(router.currentRoute.value)
      if (target) router.replace(target)
      return
    }
    applyError.value = err.message || '没撤回成'
  } finally {
    cancelDeductionId.value = null
  }
}

function startRejectDeduction(deduction) {
  rejectingDeductionId.value = deduction.id
  rejectReason.value = ''
  reviewError.value = ''
}

/** 店长批 / 驳一张抵扣单（`/review/deductions/{id}/{action}`）。 */
async function decideDeduction(deduction, action, body = {}) {
  if (reviewBusyId.value) return
  reviewBusyId.value = deduction.id
  reviewError.value = ''
  note.value = ''
  try {
    await staffRequest(`/api/overtime/review/deductions/${deduction.id}/${action}`, {
      method: 'POST',
      body,
    })
    note.value = action === 'approve' ? '抵扣批了，他手机上就能看到' : '抵扣驳回了，理由会给他看到'
    rejectingDeductionId.value = null
    rejectReason.value = ''
    await load(true)
  } catch (err) {
    if (err.status === 401) {
      const target = loginRedirectTarget(router.currentRoute.value)
      if (target) router.replace(target)
      return
    }
    reviewError.value = err.message || '没处理成'
  } finally {
    reviewBusyId.value = null
  }
}

// ── 店长那一面（票 04）：待审批队列、批 / 驳、代录选人 ────────────────────────

/** 拉待审批队列与选人名单。两者都只认「加班与补钟审批」能力（没给的人调它 403）。 */
async function loadReview() {
  reviewError.value = ''
  try {
    const [queue, people] = await Promise.all([
      staffRequest('/api/overtime/review/pending'),
      staffRequest('/api/overtime/review/employees'),
    ])
    pending.value = queue.entries || []
    pendingDeductions.value = queue.deductions || []
    roster.value = people.employees || []
  } catch (err) {
    if (err.status === 401) {
      const target = loginRedirectTarget(router.currentRoute.value)
      if (target) router.replace(target)
      return
    }
    reviewError.value = err.message || '读不到等着批的登记'
  }
}

function startReject(entry) {
  rejectingId.value = entry.id
  rejectReason.value = ''
  reviewError.value = ''
}

/** 批或驳一笔。`body` 只在驳回时带理由 —— 服务端强制它非空。 */
async function decide(entry, action, body = {}) {
  if (reviewBusyId.value) return
  reviewBusyId.value = entry.id
  reviewError.value = ''
  note.value = ''
  try {
    await staffRequest(`/api/overtime/review/${entry.id}/${action}`, {
      method: 'POST',
      body,
    })
    note.value = action === 'approve' ? '批了，他手机上就能看到' : '驳回了，理由会给他看到'
    rejectingId.value = null
    rejectReason.value = ''
    await load(true)
  } catch (err) {
    if (err.status === 401) {
      const target = loginRedirectTarget(router.currentRoute.value)
      if (target) router.replace(target)
      return
    }
    reviewError.value = err.message || '没处理成'
  } finally {
    reviewBusyId.value = null
  }
}

onMounted(() => {
  document.title = workbenchDocumentTitle('加班与补钟')
  load()
})
// 实时（票 02）：这一笔被批了 / 驳了 / 作废了，自己这台设备上的状态要跟着变 ——
// 员工不用反复下拉刷新才知道「批没批」。quiet 重读：状态变化不值得把整页打回 loading。
useNudgePull({ id: 'me-overtime', topics: ['overtime'], pull: () => load(true) })
</script>

<template>
  <div class="overtime-page hygiene-staff">
    <header class="tTop">
      <div class="tTop-main">
        <p class="kicker">我的工时</p>
        <h1 class="tDay">加班与补钟</h1>
        <p class="tDate">{{ employee ? employee.name : '' }}</p>
      </div>
    </header>

    <p v-if="state === 'loading'" class="mSub">正在读你的加班记录…</p>

    <template v-else-if="state === 'error'">
      <p class="mSub">{{ errorText }}</p>
      <div class="oBody">
        <button class="btn btn-block" type="button" @click="load()">重试</button>
      </div>
    </template>

    <div v-else class="oBody">
      <!-- 本月：两格读数。徽章各自说清算不算数，不再用一句话解释。 -->
      <section v-if="summary" class="panel">
        <div class="panel-hd">
          <h2>这个月</h2>
          <span class="hint">{{ summary.month }}</span>
        </div>
        <dl class="stats">
          <div class="stat">
            <dt><span class="badge ok">净时长</span></dt>
            <dd>{{ formatHalfHours(summary.approved.net_half_hours) }}<i>小时</i></dd>
          </div>
          <div class="stat">
            <dt><span class="badge warn">待批</span></dt>
            <dd>{{ formatHalfHours(summary.pending.net_half_hours) }}<i>小时</i></dd>
          </div>
          <div class="stat">
            <dt><span class="badge">已抵存假</span></dt>
            <dd>{{ summary.approved.deducted_days || 0 }}<i>天</i></dd>
          </div>
        </dl>
      </section>

      <!-- 店长那一面（票 04）：打开页面第一件事是「有什么等着我批」，所以它在登记
           表单前面。没有这个能力的人根本看不到这一块（服务端 403 兜底）。 -->
      <section v-if="canReview" class="panel oReview">
        <div class="panel-hd">
          <h2>等着我批的</h2>
          <span class="hint">{{ pending.length }} 笔</span>
        </div>
        <p v-if="reviewError" class="oErr">{{ reviewError }}</p>
        <ul v-if="pending.length" class="oList">
          <li v-for="entry in pending" :key="entry.id" class="oItem">
            <div class="oItem-top">
              <span class="oDate">{{ entry.entry_date }}</span>
              <span class="oPill wait">{{ entry.employee_name }}</span>
            </div>
            <p class="oItem-mid">
              <b class="oH" :class="{ minus: entry.half_hours < 0 }">
                {{ formatHalfHours(entry.half_hours) }}<i>小时</i>
              </b>
              <span class="oReason">{{ entry.reason }}</span>
            </p>
            <p class="oWho">{{ submittedByText(entry) }}</p>
            <div class="oActs">
              <button
                class="btn oAct ok"
                type="button"
                :disabled="reviewBusyId === entry.id"
                @click="decide(entry, 'approve')"
              >
                批准
              </button>
              <button
                class="btn oAct bad"
                type="button"
                :disabled="reviewBusyId === entry.id"
                @click="startReject(entry)"
              >
                驳回
              </button>
            </div>
            <div v-if="rejectingId === entry.id" class="oReject">
              <input
                v-model="rejectReason"
                class="input"
                type="text"
                :maxlength="maxRejectReason"
                placeholder="为什么不算（必填，员工看得到）"
              />
              <button
                class="btn oAct bad"
                type="button"
                :disabled="!rejectReason.trim() || reviewBusyId === entry.id"
                @click="decide(entry, 'reject', { reason: rejectReason.trim() })"
              >
                确认驳回
              </button>
            </div>
          </li>
        </ul>
        <ul v-if="pendingDeductions.length" class="oList">
          <li v-for="item in pendingDeductions" :key="`d-${item.id}`" class="oItem">
            <div class="oItem-top">
              <span class="oDate">{{ item.month }}</span>
              <span class="oPill wait">{{ item.employee_name }} · 抵扣</span>
            </div>
            <p class="oItem-mid">
              <b class="oH">{{ item.days }}<i>天</i></b>
              <span class="oReason">{{ deductionText(item) }}</span>
            </p>
            <div class="oActs">
              <button
                class="btn oAct ok"
                type="button"
                :disabled="reviewBusyId === item.id"
                @click="decideDeduction(item, 'approve')"
              >
                批准
              </button>
              <button
                class="btn oAct bad"
                type="button"
                :disabled="reviewBusyId === item.id"
                @click="startRejectDeduction(item)"
              >
                驳回
              </button>
            </div>
            <div v-if="rejectingDeductionId === item.id" class="oReject">
              <input
                v-model="rejectReason"
                class="input"
                type="text"
                :maxlength="maxRejectReason"
                placeholder="为什么不算（必填，员工看得到）"
              />
              <button
                class="btn oAct bad"
                type="button"
                :disabled="!rejectReason.trim() || reviewBusyId === item.id"
                @click="decideDeduction(item, 'reject', { reason: rejectReason.trim() })"
              >
                确认驳回
              </button>
            </div>
          </li>
        </ul>
        <p v-if="!pending.length && !pendingDeductions.length" class="mSub oEmpty">
          没有等着你批的登记。
        </p>
      </section>

      <form class="panel oForm" @submit.prevent="submit">
        <div class="panel-hd">
          <h2>记一笔</h2>
          <span class="hint">{{ canReview ? '可补录过去' : '今天 · 昨天' }}</span>
        </div>
        <div class="oPad">
          <!-- 店长能补录任意过去日期，所以给他一个日期输入；员工只有今天 / 昨天两个
               选项，那两个按钮本身就是「窗口」的可视化。 -->
          <div v-if="canReview" class="row">
            <span class="row-lb">哪一天</span>
            <!-- 日期用站内组件（2026-10-13 用户裁定「日期格式要中国本土化」）：原生
                 `<input type=date>` 的显示格式跟着**浏览器界面语言**走，页面控制不了。 -->
            <LuyunDatePicker
              v-model="day"
              :max-date="today"
              placeholder="选择日期"
              aria-label="补录哪一天"
            />
          </div>
          <div v-else class="row">
            <span class="row-lb">哪一天</span>
            <div class="days">
              <button
                v-for="opt in dayOptions"
                :key="opt.value"
                class="day"
                :class="{ on: day === opt.value }"
                type="button"
                @click="day = opt.value"
              >
                {{ opt.label }}
              </button>
            </div>
          </div>

          <div v-if="canReview && roster.length" class="row">
            <span class="row-lb">替谁记</span>
            <select v-model="backfillFor" class="input select">
              <option value="">我自己</option>
              <option v-for="person in roster" :key="person.id" :value="String(person.id)">
                {{ person.name }}{{ person.resigned ? '（已离职）' : '' }}
              </option>
            </select>
          </div>

          <div class="row">
            <span class="row-lb">时长</span>
            <div class="stepper">
              <button
                class="step"
                type="button"
                aria-label="减半小时"
                @click="halfHours = stepHalfHours(halfHours, -1, maxHalfHours)"
              >
                −
              </button>
              <p class="readout">
                <b class="hours" :class="{ minus: halfHours < 0 }">{{ hoursText }}</b><i>小时</i>
              </p>
              <button
                class="step"
                type="button"
                aria-label="加半小时"
                @click="halfHours = stepHalfHours(halfHours, 1, maxHalfHours)"
              >
                +
              </button>
              <!-- 读数后面补一句「加班 / 补钟」：这一页名字就叫「加班与补钟」，光看正负号
                   不够直白。0 时既不是加班也不是补钟，写「—」。 -->
              <span
                class="kind"
                :class="{ 'is-overtime': halfHours > 0, 'is-clock': halfHours < 0 }"
              >{{ halfHours > 0 ? '加班' : halfHours < 0 ? '补钟' : '—' }}</span>
            </div>
          </div>

          <div class="row">
            <span class="row-lb">事由</span>
            <input
              v-model="reason"
              class="input"
              type="text"
              :maxlength="maxReason"
              placeholder="加班原因（必填）"
            />
          </div>

          <p v-if="formError" class="oErr">{{ formError }}</p>
          <p v-else-if="note" class="oNote">{{ note }}</p>
          <button class="btn btn-primary btn-block" type="submit" :disabled="!canSubmit">
            {{ busy ? '提交中…' : '提交登记' }}
          </button>
          <p class="oRule">
            加班记正数、补钟记负数，1 格 = 0.5 小时，单笔最多 {{ maxHalfHours / 2 }} 小时。
            只能登记今天与昨天，更早的请找店长补录。用存假抵补钟：8.5 小时 = 1 天、只能整天，
            归到补钟所在那个月，封账后不能再抵；净时长已扣掉抵过的那部分。
          </p>
        </div>
      </form>

      <!-- 补钟用存假抵扣（ADR 0124）：8.5 小时 = 1 天、只能整天；可抵天数由服务端
           按「已批补钟 − 已抵」与「存假余额」两头封顶后带下来，这里不重复推一次 ——
           弹出「可抵 2 天」、点下去却被拒是最糟的一种。 -->
      <section v-if="capacity" class="panel">
        <div class="panel-hd">
          <h2>存假抵扣</h2>
          <span class="hint">{{ capacity.sealed ? '本月已封账' : `可抵 ${capacity.max_days} 天` }}</span>
        </div>
        <div class="oPad">
          <p class="oRule">{{ capacityText }}</p>
          <p v-if="capacity.sealed" class="oRule">已封账</p>
          <template v-else-if="capacity.max_days > 0">
            <button v-if="!applyOpen" class="btn btn-block" type="button" @click="openApply">
              申请抵扣
            </button>
            <template v-else>
              <div class="row">
                <span class="row-lb">抵几天</span>
                <div class="stepper">
                  <button
                    class="step"
                    type="button"
                    aria-label="减一天"
                    @click="applyDays = Math.max(1, applyDays - 1)"
                  >
                    −
                  </button>
                  <p class="readout"><b class="hours">{{ applyDays }}</b><i>天</i></p>
                  <button
                    class="step"
                    type="button"
                    aria-label="加一天"
                    @click="applyDays = Math.min(capacity.max_days, applyDays + 1)"
                  >
                    +
                  </button>
                </div>
              </div>
              <p class="oRule">{{ applyPreview }}</p>
              <p v-if="applyError" class="oErr">{{ applyError }}</p>
              <button
                class="btn btn-primary btn-block"
                type="button"
                :disabled="applyBusy"
                @click="applyDeduction"
              >
                {{ applyBusy ? '提交中…' : '提交申请' }}
              </button>
              <button class="btn btn-block" type="button" @click="applyOpen = false">
                先不抵
              </button>
            </template>
          </template>
          <p v-else-if="deductionEmptyText" class="oRule">{{ deductionEmptyText }}</p>
        </div>
      </section>

      <section class="panel">
        <div class="panel-hd">
          <h2>我的记录</h2>
          <span class="hint">{{ entries.length }} 笔</span>
        </div>
        <ul v-if="entries.length" class="oList">
          <li v-for="entry in entries" :key="entry.id" class="oItem">
            <div class="oItem-top">
              <span class="oDate">{{ entry.entry_date }}</span>
              <span class="oPill" :class="entryStatusTone(entry.status)">
                {{ entryStatusText(entry.status) }}
              </span>
            </div>
            <p class="oItem-mid">
              <b class="oH" :class="{ minus: entry.half_hours < 0 }">
                {{ formatHalfHours(entry.half_hours) }}<i>小时</i>
              </b>
              <span class="oReason">{{ entry.reason }}</span>
            </p>
            <p v-if="entry.reject_reason" class="oErr">驳回理由：{{ entry.reject_reason }}</p>
            <!-- 驳回之后「重提一笔」并不总是可行：这笔要是前天以前的，员工自己已经登不了
                 （窗口只有今天与昨天）。不说这一句，员工会以为随时能补，然后卡在日期上。 -->
            <p v-if="entry.status === 'rejected'" class="mSub">
              这一笔不算数。重提一笔就行；日期已经过了窗口（只能登今天与昨天）的话，请店长补录。
            </p>
            <button
              v-if="entry.status === 'pending'"
              class="oCancel"
              type="button"
              :disabled="cancelBusyId === entry.id"
              @click="cancelEntry(entry)"
            >
              {{ cancelBusyId === entry.id ? '撤回中…' : '撤回' }}
            </button>
          </li>
        </ul>
        <p v-else class="mSub oEmpty">
          还没有登记过。加班记一笔、欠钟记一笔，都会出现在这里。
        </p>
      </section>

      <section class="panel">
        <div class="panel-hd">
          <h2>我的抵扣</h2>
          <span class="hint">{{ deductions.length }} 张</span>
        </div>
        <ul v-if="deductions.length" class="oList">
          <li v-for="item in deductions" :key="item.id" class="oItem">
            <div class="oItem-top">
              <span class="oDate">{{ item.month }}</span>
              <span class="oPill" :class="entryStatusTone(item.status)">
                {{ entryStatusText(item.status) }}
              </span>
            </div>
            <p class="oItem-mid">
              <b class="oH">{{ item.days }}<i>天</i></b>
              <span class="oReason">{{ deductionText(item) }}</span>
            </p>
            <p v-if="item.reject_reason" class="oErr">驳回理由：{{ item.reject_reason }}</p>
            <button
              v-if="item.status === 'pending'"
              class="oCancel"
              type="button"
              :disabled="cancelDeductionId === item.id"
              @click="cancelDeduction(item)"
            >
              {{ cancelDeductionId === item.id ? '撤回中…' : '撤回' }}
            </button>
          </li>
        </ul>
        <p v-else class="mSub oEmpty">暂无抵扣</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* 加班与补钟（员工端）—— 版式对齐「我的今天 / 我的整月」（2026-10-30）。
   元素语言与那两页同一套：页头（kicker + H1 + 姓名）、面板（.panel）、读数（mono 大字 +
   小号单位）、列表行（上沿一条发丝线）。颜色只走语义令牌，深浅两套主题自动跟随。
   原来这一页的页头与空态文字**没有任何样式**（`.tTop` / `.mSub` 只长在别的页的 scoped
   作用域里），标题、姓名、空态说明全按浏览器默认 16px 渲染 —— 这是「布局散」的主要原因。 */
.overtime-page {
  min-height: 100%;
  background: var(--surface-0);
  color: var(--ink);
  font-family: var(--font-sans);
  padding-bottom: 28px;
}

/* ── 页头（尺寸与整月页同一档）────────────────────────────────────────── */
.tTop {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  padding: max(18px, var(--safe-t)) max(16px, var(--safe-r)) 14px max(16px, var(--safe-l));
}
.tTop-main { min-width: 0; }
.kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}
.tDay {
  margin: 0;
  font: 600 26px/1 var(--font-sans);
  letter-spacing: .01em;
  color: var(--ink);
}
.tDate { margin: 7px 0 0; font-size: 10.5px; color: var(--ink-dim); }

/* ── 面板 ──────────────────────────────────────────────────────────────── */
.oBody {
  padding: 0 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.panel {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  box-shadow: var(--hy-shadow-sm);
  overflow: hidden;
}
.panel-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--line);
}
.panel-hd h2 {
  margin: 0;
  font: 600 12.5px/1 var(--font-sans);
  letter-spacing: .06em;
  color: var(--ink);
}
.hint {
  margin-left: auto;
  font-family: var(--font-mono), var(--font-sans);
  font-size: 9.5px;
  line-height: 1;
  color: var(--ink-faint);
}

/* ── 本月读数：两格 ───────────────────────────────────────────────────── */
.stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin: 0;
  padding: 12px 14px 14px;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 11px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
}
.stat dt { margin: 0; }
.stat dd {
  display: flex;
  align-items: baseline;
  margin: 0;
  font-family: var(--font-mono), var(--font-sans);
  font-size: 19px;
  font-weight: 600;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
/* 徽章：全局 `.badge` 只有一种灰，`ok` / `warn` 两个后缀从没定义过 —— 原来「已批准」
   与「待批」长得一模一样。这里把两种状态各自上色。 */
.badge {
  padding: 2px 7px;
  font-size: 9.5px;
  line-height: 1.6;
  border-radius: var(--r-full);
}
.badge.ok { color: var(--ok); border-color: var(--ok); background: var(--ok-soft); }
.badge.warn { color: var(--warn); border-color: var(--warn); background: var(--warn-soft); }

/* 单位：读数后面的小号「小时」，与读数同一基线。 */
.stat dd i,
.oItem-mid .oH i,
.readout i {
  margin-left: 3px;
  font-family: var(--font-sans);
  font-style: normal;
  font-weight: 500;
  font-size: 10px;
  color: var(--ink-faint);
}

/* ── 表单 ──────────────────────────────────────────────────────────────── */
.oPad {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px 14px;
}
.row { display: flex; align-items: center; gap: 10px; }
.row-lb {
  flex: none;
  width: 48px;
  font-size: 11.5px;
  color: var(--ink-dim);
}
.row > .input { flex: 1; min-width: 0; }
.days { display: flex; gap: 8px; }
.day {
  min-height: 48px;
  padding: 0 18px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  background: transparent;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  cursor: pointer;
  transition: border-color .16s var(--ease), color .16s var(--ease), background .16s var(--ease);
}
.day.on {
  color: var(--action);
  border-color: var(--action);
  background: var(--action-soft);
  font-weight: 600;
}

/* 时长：两侧是**正方形**加减键（原来 34×34 的圆 + 共享表给的 44px 下限，实测渲染成
   34×44 的竖椭圆）。按下限 48 的方角按钮重做，读数夹在中间。 */
.stepper {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.step {
  flex: none;
  width: 48px;
  height: 48px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: inherit;
  font-size: 19px;
  line-height: 1;
  color: var(--ink);
  background: var(--surface-2);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  cursor: pointer;
  transition: border-color .16s var(--ease), color .16s var(--ease), background .16s var(--ease);
}
.step:hover:not(:disabled) {
  color: var(--action);
  border-color: var(--action);
  background: var(--action-soft);
}
.step:active:not(:disabled) { transform: translateY(1px); }
.readout {
  display: flex;
  align-items: baseline;
  justify-content: center;
  min-width: 74px;
  margin: 0;
}
.hours {
  font-family: var(--font-mono), var(--font-sans);
  font-size: 24px;
  font-weight: 600;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.hours.minus { color: var(--danger-bright); }
.kind {
  margin-left: 2px;
  padding: 3px 8px;
  font-size: 9.5px;
  letter-spacing: .06em;
  white-space: nowrap;
  color: var(--ink-faint);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-full);
}
.kind.is-overtime { color: var(--ok); border-color: var(--ok); background: var(--ok-soft); }
.kind.is-clock { color: var(--warn); border-color: var(--warn); background: var(--warn-soft); }

/* 日期组件（店长补录那一档）：尺寸照 `.input` 补，组件渲染的是 `.dp--input`。 */
.row :deep(.luyun-date-picker) { flex: 1; min-width: 0; }
.row :deep(.luyun-date-picker .dp--input) { width: 100%; font-size: 13px; }

.oErr { margin: 0; font-size: 11.5px; line-height: 1.6; color: var(--danger-bright); }
.oNote { margin: 0; font-size: 11.5px; line-height: 1.6; color: var(--ok); }
/* 提交按钮：主按钮、48px 方角。禁用时**不压透明度** —— 压到 0.45 之后整块糊成一格
   灰影子，看不出是个按钮（原版就是这样）；改成一格浅底 + 次级字色，仍读得出「待填完」。 */
.overtime-page.hygiene-staff .btn {
  min-height: 48px !important;
  border-radius: var(--r-1);
}
.overtime-page.hygiene-staff .btn-primary {
  background: var(--action);
  border-color: var(--action);
  color: var(--action-ink);
}
.overtime-page.hygiene-staff .btn-primary:disabled {
  opacity: 1;
  background: var(--surface-3);
  border-color: var(--line);
  color: var(--ink-dim);
}
.oRule {
  margin: 2px 0 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--ink-faint);
}

/* 直接挂在面板里（不在 .oPad 里）的那一句：自己让出横向内边距，别贴着面板边。 */
.oRulePad { padding: 0 14px 12px; }

/* ── 列表（我的记录 / 等批）──────────────────────────────────────────── */
.oList { list-style: none; margin: 0; padding: 0; }
.oItem { padding: 12px 14px; border-top: 1px solid var(--line); }
.oItem:first-child { border-top: 0; }
.oItem-top { display: flex; align-items: center; gap: 8px; }
.oDate {
  font-family: var(--font-mono), var(--font-sans);
  font-size: 10.5px;
  letter-spacing: .02em;
  color: var(--ink-dim);
}
.oPill {
  margin-left: auto;
  padding: 2px 8px;
  font-size: 9.5px;
  line-height: 1.6;
  white-space: nowrap;
  color: var(--ink-faint);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-full);
}
.oPill.wait { color: var(--warn); border-color: var(--warn); background: var(--warn-soft); }
.oPill.ok { color: var(--ok); border-color: var(--ok); background: var(--ok-soft); }
.oPill.bad {
  color: var(--danger-bright);
  border-color: var(--danger-bright);
  background: var(--danger-soft);
}
.oItem-mid {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 7px 0 0;
}
.oH {
  flex: none;
  font-family: var(--font-mono), var(--font-sans);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.oH.minus { color: var(--danger-bright); }
.oReason {
  min-width: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-dim);
  overflow-wrap: anywhere;
}
.oWho { margin: 4px 0 0; font-size: 10.5px; color: var(--ink-faint); }
.oCancel {
  margin-top: 9px;
  min-height: 44px;
  padding: 0 16px;
  font-family: inherit;
  font-size: 12px;
  color: var(--ink);
  background: transparent;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  cursor: pointer;
}
.oCancel:hover:not(:disabled) {
  color: var(--danger-bright);
  border-color: var(--danger-bright);
}
/* 面板里的空态：不带 `.mSub` 那份页级左右外边距（面板自己有 14px 内距）。 */
.panel .oEmpty { margin: 0; padding: 14px; }
/* 条目内的说明（驳回后的窗口提示）跟事由左对齐；`.mSub` 那份页级外边距在面板里会多缩进一道。 */
.oItem .mSub { margin: 6px 0 0; }

/* ── 店长那一面：批 / 驳 ──────────────────────────────────────────────── */
.oActs { display: flex; gap: 8px; margin-top: 10px; }
.oAct { flex: 1; }
.overtime-page.hygiene-staff .oAct.ok {
  color: var(--ok);
  border-color: var(--ok);
  background: transparent;
}
.overtime-page.hygiene-staff .oAct.ok:hover:not(:disabled) {
  color: var(--ok);
  border-color: var(--ok);
  background: var(--ok-soft);
}
.overtime-page.hygiene-staff .oAct.bad {
  color: var(--danger-bright);
  border-color: var(--danger-bright);
  background: transparent;
}
.overtime-page.hygiene-staff .oAct.bad:hover:not(:disabled) {
  color: var(--danger-bright);
  border-color: var(--danger-bright);
  background: var(--danger-soft);
}
.oReject { display: flex; gap: 8px; align-items: center; margin-top: 9px; }
.oReject .input { flex: 1; min-width: 0; }
.oReject .oAct { flex: none; }

/* 空态 / 加载 / 错误那一句话：原来这页没有 `.mSub` 样式，16px 黑字比标题还抢眼。 */
.mSub {
  margin: 0 16px 10px;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--ink-dim);
}
</style>
