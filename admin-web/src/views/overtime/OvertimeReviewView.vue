<script setup>
/**
 * 人事组里的「加班补钟审批」页（2026-11-03 用户裁定）。
 *
 * 来历：原来有两页 —— 员工手机端 `/workbench/staff/overtime`（自己提、看自己的）和
 * 管理端「加班统计」`/workbench/hr/overtime`（超管的审批台 + 台账 + 加班费）。用户
 * 裁定把审批独立成一页放进人事组，随后又裁定**把「加班统计」并进来**：现在这一页
 * 就是加班与补钟的**唯一管理页**，审批、代录、台账、加班费都在这里。
 *
 * **一页两种身份**（页面清单 `audience: admin` + `staffCap: schedule`）：
 *   - 超管（管理端会话）走 `/api/overtime/admin/*`：审批 + 代录 + 台账 + 加班费 + 作废；
 *   - 勾了「人事管理」的店长（员工会话）走 `/api/overtime/review/*`：只有审批 + 代录。
 *     **金额与全店台账不给他** —— 底薪不下发员工端（ADR 0098），这是产品边界不是参数。
 * 两套端点落在同一个服务层（`OvertimeLedger`），审批规则只有一份。
 *
 * 版式：桌面两栏（左：等审批 / 台账 / 加班费；右：代员工补录），窄屏单列，
 * DOM 顺序就是手机上的阅读顺序（概览 → 审批 → 代录 → 台账 → 加班费）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import { api } from '../../api/client'
import { hasCap } from '../../utils/adminCaps'
import { isLoggedIn } from '../../utils/authStatus'
import { staffRequest } from '../../utils/hygieneStaff'
import { staffCapsProbe } from '../../utils/staffCaps'
import { useNudgePull } from '../../composables/useNudgePull'
import { WORKBENCH_HR_HOME, workbenchDocumentTitle } from '../../utils/workbenchCopy'
import {
  HALF_HOURS_MAX,
  MAX_REASON,
  MAX_REJECT_REASON,
  entryKindText,
  entryStatusText,
  entryStatusTone,
  formatHalfHours,
  isSelfSubmitted,
  stepHalfHours,
  submittedByText,
} from '../../utils/overtimeEntry'

const router = useRouter()

/** 两套端点：同一个服务层，两条门。`{id}` 由调用处拼。 */
const ENDPOINTS = {
  admin: {
    pending: '/api/overtime/admin/pending',
    employees: '/api/overtime/admin/employees',
    entries: '/api/overtime/admin/entries',
    month: '/api/overtime/admin/month',
    backfill: '/api/overtime/admin/entries',
    entryApprove: (id) => `/api/overtime/admin/entries/${id}/approve`,
    entryReject: (id) => `/api/overtime/admin/entries/${id}/reject`,
    entryVoid: (id) => `/api/overtime/admin/entries/${id}/void`,
    deductionApprove: (id) => `/api/overtime/admin/deductions/${id}/approve`,
    deductionReject: (id) => `/api/overtime/admin/deductions/${id}/reject`,
  },
  staff: {
    pending: '/api/overtime/review/pending',
    employees: '/api/overtime/review/employees',
    backfill: '/api/overtime/review/entries',
    entryApprove: (id) => `/api/overtime/review/${id}/approve`,
    entryReject: (id) => `/api/overtime/review/${id}/reject`,
    deductionApprove: (id) => `/api/overtime/review/deductions/${id}/approve`,
    deductionReject: (id) => `/api/overtime/review/deductions/${id}/reject`,
  },
}

const state = ref('loading') // 'loading' | 'ready' | 'error'
const mode = ref('') // ''（未判定）| 'admin' | 'staff' | 'anonymous' | 'unknown'
const errorText = ref('')
const receipt = ref('')
const today = ref('')
const queue = ref([]) // 等审批的加班 / 补钟登记（旧的在前）
const pendingDeductions = ref([]) // 等审批的存假抵扣单（ADR 0124，同一个队列）
const employees = ref([])
const entries = ref([]) // 全店台账（仅管理端，新的在前，可按月筛）
const month = ref('') // '' = 本月；台账与加班费一起跟它走
const stats = ref(null) // 加班费账单（仅管理端）
const statsError = ref('')
const busyId = ref(0) // 行内动作的单槽（同排班待办页）
const formBusy = ref(false)
const rejectTarget = ref(null)
const rejectReason = ref('')
const rejectDeductionTarget = ref(null)
const voidTarget = ref(null)

// 上限随响应下发（`/pending` 带 `max_*`）：页面不写死第二份。
const maxReason = ref(MAX_REASON)
const maxHalfHours = ref(HALF_HOURS_MAX)
const maxRejectReason = ref(MAX_REJECT_REASON)

const form = ref({ employee_id: '', entry_date: '', half_hours: 1, reason: '' })
const formError = ref('')

const endpoints = computed(() => ENDPOINTS[mode.value] || null)
const canReview = computed(() => mode.value === 'admin' || mode.value === 'staff')
const isAdmin = computed(() => mode.value === 'admin')
const canBackfill = computed(
  () =>
    !!form.value.employee_id &&
    !!form.value.entry_date &&
    !!form.value.reason.trim() &&
    !formBusy.value,
)
const pickable = computed(() => employees.value.filter((person) => !person.resigned))
const pendingCount = computed(() => queue.value.length + pendingDeductions.value.length)

const modeHint = computed(() => {
  if (mode.value === 'anonymous') {
    return '这一页要登录：管理端账号，或花名册上勾了「人事管理」的员工手机账号。'
  }
  if (mode.value === 'unknown') {
    return '登录状态没问到（网络或服务暂时不通），刷新一下再试。'
  }
  return ''
})

function employeeName(id) {
  const found = employees.value.find((person) => person.id === id)
  return found ? found.name : ''
}

function deductionText(item) {
  return `抵 ${item.hours} 小时补钟`
}

/** 概览里的净时长**不带符号**：`formatHalfHours` 给 0 会出「−0」，而概览读的是
 *  「这个月一共多少小时」，不是一笔带符号的流水。 */
function hoursText(halfHours) {
  const hours = Number(halfHours || 0) / 2
  return Number.isInteger(hours) ? String(hours) : hours.toFixed(1)
}

/** 这一页用哪一套端点：管理端会话优先，其次员工会话 + 人事管理能力。 */
async function detectMode() {
  if (await isLoggedIn()) return 'admin'
  const probe = await staffCapsProbe()
  if (probe.state === 'ok' && hasCap(probe.caps, 'schedule')) return 'staff'
  if (probe.state === 'unauthenticated') return 'anonymous'
  return 'unknown'
}

/** 两套门都吃同一套形状，只有 GET / POST 的区别（`api` 与 `staffRequest` 的签名不同）。 */
async function call(path, { method = 'GET', body } = {}) {
  if (isAdmin.value) {
    return method === 'GET' ? api.get(path) : api.post(path, body)
  }
  return staffRequest(path, { method, body })
}

function monthParams() {
  return month.value ? { month: month.value } : undefined
}

async function load() {
  if (!mode.value) mode.value = await detectMode()
  if (!canReview.value) {
    state.value = 'ready'
    return
  }
  state.value = 'loading'
  errorText.value = ''
  try {
    const jobs = [call(endpoints.value.pending), call(endpoints.value.employees)]
    // 台账与加班费只给管理端：员工会话没有这两个端点（底薪不下发员工端）。
    if (isAdmin.value) jobs.push(api.get(endpoints.value.entries, monthParams()))
    const [pendingData, people, listing] = await Promise.all(jobs)
    today.value = pendingData.today || ''
    queue.value = pendingData.entries || []
    pendingDeductions.value = pendingData.deductions || []
    employees.value = people.employees || []
    if (listing) entries.value = listing.entries || []
    maxReason.value = pendingData.max_reason || MAX_REASON
    maxHalfHours.value = pendingData.max_half_hours || HALF_HOURS_MAX
    maxRejectReason.value = pendingData.max_reject_reason || MAX_REJECT_REASON
    if (!form.value.entry_date) form.value.entry_date = today.value
    state.value = 'ready'
  } catch (err) {
    errorText.value = err.message || '读不到等着批的登记'
    state.value = 'error'
    return
  }
  if (isAdmin.value) await loadStats()
}

/** 写失败：先把服务端那句话留住，重读队列，**最后**再说出来（同排班待办页的口径）。 */
async function reportFailure(err, fallback) {
  const message = err.message || fallback
  await load()
  errorText.value = message
}

async function decide(entry, action) {
  if (busyId.value) return
  busyId.value = entry.id
  errorText.value = ''
  receipt.value = ''
  const key =
    action === 'approve' ? 'entryApprove' : action === 'void' ? 'entryVoid' : 'entryReject'
  try {
    await call(endpoints.value[key](entry.id), {
      method: 'POST',
      body: action === 'reject' ? { reason: rejectReason.value.trim() } : undefined,
    })
    const kind = entryKindText(entry.kind)
    receipt.value =
      action === 'approve'
        ? `批了 ${entry.employee_name || '这一笔'} 的${kind}`
        : action === 'void'
          ? `作废了 ${entry.employee_name || '这一笔'} 的${kind}：记录没删，状态是已作废`
          : `驳回了 ${entry.employee_name || '这一笔'} 的${kind}，理由员工看得到`
    rejectTarget.value = null
    rejectReason.value = ''
    await load()
  } catch (err) {
    // 可能是别人先批了、员工自己撤了：重读一次，别对着一条已经处理过的反复点。
    await reportFailure(err, '没处理成')
  } finally {
    if (busyId.value === entry.id) busyId.value = 0
  }
}

function startReject(entry) {
  rejectTarget.value = entry
  rejectReason.value = ''
  errorText.value = ''
  receipt.value = ''
}

async function confirmReject() {
  const entry = rejectTarget.value
  if (entry) await decide(entry, 'reject')
}

function startVoid(entry) {
  voidTarget.value = entry
}

async function confirmVoid() {
  const entry = voidTarget.value
  voidTarget.value = null
  if (entry) await decide(entry, 'void')
}

async function decideDeduction(item, action) {
  if (busyId.value) return
  busyId.value = item.id
  errorText.value = ''
  receipt.value = ''
  try {
    await call(
      endpoints.value[action === 'approve' ? 'deductionApprove' : 'deductionReject'](item.id),
      {
        method: 'POST',
        body: action === 'reject' ? { reason: rejectReason.value.trim() } : undefined,
      },
    )
    receipt.value =
      action === 'approve'
        ? `批了 ${item.employee_name || '这一笔'} 的存假抵扣`
        : `驳回了 ${item.employee_name || '这一笔'} 的存假抵扣，理由员工看得到`
    rejectDeductionTarget.value = null
    rejectReason.value = ''
    await load()
  } catch (err) {
    await reportFailure(err, '没处理成')
  } finally {
    if (busyId.value === item.id) busyId.value = 0
  }
}

function startRejectDeduction(item) {
  rejectDeductionTarget.value = item
  rejectReason.value = ''
  errorText.value = ''
  receipt.value = ''
}

async function confirmRejectDeduction() {
  const item = rejectDeductionTarget.value
  if (item) await decideDeduction(item, 'reject')
}

async function loadListing() {
  if (!isAdmin.value) return
  try {
    const listing = await api.get(endpoints.value.entries, monthParams())
    entries.value = listing.entries || []
  } catch (err) {
    errorText.value = err.message || '读不出这个月的台账'
  }
  await loadStats()
}

/** 这个月的加班费账单。金额只用**快照底薪**算，每人各自取整到元、合计是各行相加 ——
 *  口径全在服务端（`OvertimeLedger.monthly_stats`），这一层只负责摆出来。
 *
 *  这一块读失败只让这一块报错：审批队列与台账都还能用，不该因为账单读不出来整页变灰。 */
async function loadStats() {
  statsError.value = ''
  try {
    stats.value = (await api.get(endpoints.value.month, monthParams())).stats
  } catch (err) {
    stats.value = null
    statsError.value = err.message || '这个月的加班费没读出来'
  }
}

async function backfill() {
  if (formBusy.value) return
  formBusy.value = true
  formError.value = ''
  receipt.value = ''
  const who = employeeName(form.value.employee_id)
  try {
    await call(endpoints.value.backfill, {
      method: 'POST',
      body: {
        employee_id: Number(form.value.employee_id),
        entry_date: form.value.entry_date,
        half_hours: form.value.half_hours,
        reason: form.value.reason.trim(),
      },
    })
    receipt.value = `替 ${who} 补上了 ${form.value.entry_date} 的 ${formatHalfHours(form.value.half_hours)} 小时，等你去上面批`
    form.value.reason = ''
    form.value.half_hours = 1
    await load()
  } catch (err) {
    // 服务端那句中文就是给操作的人看的（「还没到的日子登不了」这种），原样显示。
    formError.value = err.message || '没补上'
  } finally {
    formBusy.value = false
  }
}

onMounted(() => {
  document.title = workbenchDocumentTitle('加班补钟审批')
  load()
})

// 这一页本来就是「等别人动作」的地方：员工提了、撤了，队列要跟着变。
useNudgePull({
  id: 'workbench-hr-overtime-review',
  topics: ['overtime'],
  pull: () => {
    if (canReview.value) load()
  },
})
</script>

<template>
  <div class="overtime-review">
    <header class="oTop">
      <div class="oTop-main">
        <p class="oKicker">人事</p>
        <h1>加班补钟审批</h1>
        <p class="oSub">
          <template v-if="today">今天 {{ today }} · </template>
          员工在手机上提的加班、补钟与存假抵扣都在这里批；只有批过的才算数。
        </p>
      </div>
      <button class="btn" type="button" @click="router.push(WORKBENCH_HR_HOME)">回到月历</button>
    </header>

    <p v-if="state === 'loading'" class="oHint">正在读等着批的登记…</p>

    <template v-else>
      <p v-if="!canReview" class="oErr" role="alert">{{ modeHint }}</p>
      <p v-else-if="errorText" class="oErr" role="alert">{{ errorText }}</p>
      <p v-else-if="receipt" class="oOk" role="status">{{ receipt }}</p>

      <template v-if="canReview">
        <!-- 概览：一眼看今天有多少活。金额只有管理端有（底薪不下发员工端）。 -->
        <section class="oStats" aria-label="本月概览">
          <div class="oStat">
            <span class="oStat-k">待批</span>
            <b class="oStat-v">{{ pendingCount }}<i>笔</i></b>
          </div>
          <div v-if="isAdmin" class="oStat">
            <span class="oStat-k">本月净时长</span>
            <b class="oStat-v">
              {{ stats ? hoursText(stats.total.net_half_hours) : '—' }}<i>小时</i>
            </b>
          </div>
          <div v-if="isAdmin" class="oStat">
            <span class="oStat-k">本月加班费</span>
            <b class="oStat-v">
              {{ stats ? (stats.total.amount ?? '—') : '—' }}<i>元</i>
            </b>
          </div>
        </section>

        <div class="oGrid">
          <!-- ① 等审批（主卡） -->
          <section class="oCard oQueue">
            <div class="oCard-hd">
              <h2>等审批</h2>
              <span class="oCount">{{ pendingCount }}</span>
            </div>
            <p v-if="!pendingCount" class="oEmpty">
              没有等着批的登记。员工提了、或者你刚才补录了，都会立刻出现在这里。
            </p>

            <ul v-if="queue.length" class="oList">
              <li v-for="entry in queue" :key="entry.id" class="oItem">
                <div class="oRow-hd">
                  <b>{{ entry.employee_name || '（没有名字）' }}</b>
                  <span class="oDate">{{ entry.entry_date }}</span>
                  <span class="oH" :class="{ minus: entry.half_hours < 0 }">
                    {{ formatHalfHours(entry.half_hours) }} 小时
                  </span>
                  <span class="oKind">{{ entryKindText(entry.kind) }}</span>
                  <span class="oPend">等你批</span>
                </div>
                <p class="oReason">{{ entry.reason }}</p>
                <p v-if="!isSelfSubmitted(entry)" class="oBy">{{ submittedByText(entry) }}</p>
                <div class="oAct">
                  <button
                    class="btn btn-primary"
                    type="button"
                    :disabled="busyId !== 0"
                    @click="decide(entry, 'approve')"
                  >
                    批准
                  </button>
                  <button
                    class="btn btn-danger"
                    type="button"
                    :disabled="busyId !== 0"
                    @click="startReject(entry)"
                  >
                    驳回
                  </button>
                </div>
                <div v-if="rejectTarget && rejectTarget.id === entry.id" class="oReject">
                  <input
                    v-model="rejectReason"
                    class="oInput"
                    type="text"
                    :maxlength="maxRejectReason"
                    placeholder="为什么不算（必填，员工看得到）"
                  />
                  <button
                    class="btn btn-danger"
                    type="button"
                    :disabled="!rejectReason.trim() || busyId !== 0"
                    @click="confirmReject()"
                  >
                    确认驳回
                  </button>
                  <button class="btn" type="button" @click="rejectTarget = null">取消</button>
                </div>
              </li>
            </ul>

            <ul v-if="pendingDeductions.length" class="oList">
              <li v-for="item in pendingDeductions" :key="`d-${item.id}`" class="oItem">
                <div class="oRow-hd">
                  <b>{{ item.employee_name || '（没有名字）' }}</b>
                  <span class="oDate">{{ item.month }}</span>
                  <span class="oH">{{ item.days }} 天</span>
                  <span class="oKind">存假抵扣</span>
                  <span class="oPend">等你批</span>
                </div>
                <p class="oReason">{{ deductionText(item) }}</p>
                <div class="oAct">
                  <button
                    class="btn btn-primary"
                    type="button"
                    :disabled="busyId !== 0"
                    @click="decideDeduction(item, 'approve')"
                  >
                    批准
                  </button>
                  <button
                    class="btn btn-danger"
                    type="button"
                    :disabled="busyId !== 0"
                    @click="startRejectDeduction(item)"
                  >
                    驳回
                  </button>
                </div>
                <div
                  v-if="rejectDeductionTarget && rejectDeductionTarget.id === item.id"
                  class="oReject"
                >
                  <input
                    v-model="rejectReason"
                    class="oInput"
                    type="text"
                    :maxlength="maxRejectReason"
                    placeholder="为什么不算（必填，员工看得到）"
                  />
                  <button
                    class="btn btn-danger"
                    type="button"
                    :disabled="!rejectReason.trim() || busyId !== 0"
                    @click="confirmRejectDeduction()"
                  >
                    确认驳回
                  </button>
                  <button class="btn" type="button" @click="rejectDeductionTarget = null">
                    取消
                  </button>
                </div>
              </li>
            </ul>
          </section>

          <!-- ② 代员工补录（右栏；手机上排在审批后面） -->
          <section class="oCard oBackfill">
            <div class="oCard-hd">
              <h2>代员工补录</h2>
            </div>
            <p class="oLead">
              员工忘了登、或者窗口已经过了的，在这里补 —— 日期可以选任意过去的一天。
              补录走同一条流水线，所以补完它还是待审批，得再批一次才算数。
            </p>
            <form class="oForm" @submit.prevent="backfill">
              <label class="oField">
                <span class="oLabel">补谁</span>
                <select v-model="form.employee_id" class="oInput">
                  <option value="">选一个人</option>
                  <option v-for="person in pickable" :key="person.id" :value="person.id">
                    {{ person.name }}
                  </option>
                </select>
              </label>
              <label class="oField">
                <span class="oLabel">哪一天</span>
                <!-- 日期用站内组件（2026-10-13 用户裁定「日期格式要中国本土化」）。 -->
                <LuyunDatePicker
                  v-model="form.entry_date"
                  :max-date="today"
                  placeholder="选择日期"
                  aria-label="补录哪一天"
                />
              </label>
              <div class="oField">
                <span class="oLabel">时长</span>
                <span class="oStepper">
                  <button
                    class="oStep"
                    type="button"
                    aria-label="减半小时"
                    @click="form.half_hours = stepHalfHours(form.half_hours, -1, maxHalfHours)"
                  >
                    −
                  </button>
                  <b class="oHours" :class="{ minus: form.half_hours < 0 }">
                    {{ formatHalfHours(form.half_hours) }} 小时
                  </b>
                  <button
                    class="oStep"
                    type="button"
                    aria-label="加半小时"
                    @click="form.half_hours = stepHalfHours(form.half_hours, 1, maxHalfHours)"
                  >
                    +
                  </button>
                </span>
              </div>
              <label class="oField">
                <span class="oLabel">事由</span>
                <input
                  v-model="form.reason"
                  class="oInput"
                  type="text"
                  :maxlength="maxReason"
                  placeholder="加班原因（必填）"
                />
              </label>
              <p v-if="formError" class="oErr">{{ formError }}</p>
              <button class="btn btn-primary" type="submit" :disabled="!canBackfill">
                {{ formBusy ? '补录中…' : '补录一笔' }}
              </button>
              <p class="oHint">
                加班记正数、补钟记负数，1 格 = 0.5 小时，单笔最多 {{ maxHalfHours / 2 }} 小时；
                未来日期谁也登不了。
              </p>
            </form>
          </section>

          <!-- ③ 全店台账（仅管理端） -->
          <section v-if="isAdmin" class="oCard oLedger">
            <div class="oCard-hd">
              <h2>台账</h2>
              <span class="oCount">{{ entries.length }}</span>
              <LuyunDatePicker
                v-model="month"
                class="oMonth"
                month-picker
                placeholder="按月看"
                aria-label="台账月份"
                @update:model-value="loadListing"
              />
              <button v-if="month" class="btn" type="button" @click="month = ''; loadListing()">
                看全部
              </button>
            </div>
            <p v-if="!entries.length" class="oEmpty">这个范围里还没有登记。</p>
            <ul v-else class="oList">
              <li v-for="entry in entries" :key="entry.id" class="oItem">
                <div class="oRow-hd">
                  <b>{{ entry.employee_name || '（没有名字）' }}</b>
                  <span class="oDate">{{ entry.entry_date }}</span>
                  <span class="oH" :class="{ minus: entry.half_hours < 0 }">
                    {{ formatHalfHours(entry.half_hours) }} 小时
                  </span>
                  <span class="oKind">{{ entryKindText(entry.kind) }}</span>
                  <span class="oSt" :class="entryStatusTone(entry.status)">
                    {{ entryStatusText(entry.status) }}
                  </span>
                </div>
                <p class="oReason">{{ entry.reason }}</p>
                <p v-if="!isSelfSubmitted(entry)" class="oBy">{{ submittedByText(entry) }}</p>
                <p v-if="entry.reject_reason" class="oBy">驳回理由：{{ entry.reject_reason }}</p>
                <div v-if="entry.status === 'approved'" class="oAct">
                  <button
                    class="btn btn-danger"
                    type="button"
                    :disabled="busyId !== 0"
                    @click="startVoid(entry)"
                  >
                    作废
                  </button>
                </div>
              </li>
            </ul>
          </section>

          <!-- ④ 加班费（仅管理端） -->
          <section v-if="isAdmin" class="oCard oPay">
            <div class="oCard-hd">
              <h2>加班费</h2>
              <span v-if="stats" class="oCount">{{ stats.month }}</span>
              <span v-if="stats" class="oNote">
                {{ stats.days }} 天 × 每天 {{ stats.hours_per_day }} 小时
              </span>
            </div>
            <p v-if="statsError" class="oErr">{{ statsError }}</p>
            <p v-else-if="!stats || !stats.count" class="oEmpty">
              这个月还没有批准的加班 —— 只有批过的才算钱。
            </p>
            <template v-else>
              <ul class="oList">
                <li v-for="row in stats.items" :key="row.employee_id" class="oItem">
                  <div class="oRow-hd">
                    <b>{{ row.employee_name || '（没有名字）' }}</b>
                    <span class="oH" :class="{ minus: row.net_half_hours < 0 }">
                      {{ formatHalfHours(row.net_half_hours) }} 小时
                    </span>
                    <span class="oSt" :class="{ mute: row.amount === null }">
                      {{ row.amount === null ? '底薪待补' : `${row.amount} 元` }}
                    </span>
                  </div>
                  <p class="oBy">
                    加班 {{ formatHalfHours(row.overtime_half_hours) }} · 补钟
                    {{ formatHalfHours(row.makeup_half_hours) }} · 已抵存假
                    {{ row.deducted_days }} 天（{{ row.deducted_hours }} 小时） · 底薪
                    {{ row.base_salary === null ? '待补' : `${row.base_salary} 元/月` }}
                  </p>
                </li>
              </ul>
              <div class="oRow-hd oTotal">
                <b>合计</b>
                <span class="oH">{{ formatHalfHours(stats.total.net_half_hours) }} 小时</span>
                <span class="oSt">{{ stats.total.amount }} 元</span>
              </div>
              <p v-if="stats.total.deducted_half_hours" class="oHint">
                合计里已经扣掉用存假抵过的 {{ stats.total.deducted_hours }} 小时补钟（那部分时间按休假算）。
              </p>
              <p v-if="stats.total.unpriced" class="oHint">
                还有 {{ stats.total.unpriced }} 个人没算出金额（底薪待补），他们不在合计里。
              </p>
            </template>
          </section>
        </div>
      </template>
    </template>

    <ConfirmDialog
      v-if="voidTarget"
      title="作废这一笔"
      :message="`作废「${voidTarget.employee_name || '这位员工'}」${voidTarget.entry_date} 的 ${formatHalfHours(voidTarget.half_hours)} 小时${entryKindText(voidTarget.kind)}之后，它不再算数；记录不会删掉，员工手机上会看到「已作废」。`"
      confirm-label="作废"
      danger
      @confirm="confirmVoid"
      @cancel="voidTarget = null"
    />
  </div>
</template>

<style scoped>
/* 版式：深青墨底 + 一张卡一个话题。桌面两栏（左：审批 / 台账 / 加班费，右：代录），
   窄屏单列 —— DOM 顺序就是手机上的阅读顺序。令牌来自 /hygiene-admin.css（壳一层加载）。 */
.overtime-review { padding: 18px 16px 40px; max-width: 1040px; margin: 0 auto; }
.oTop { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
.oTop-main { min-width: 0; }
.oTop h1 { margin: 2px 0 4px; font-size: 20px; color: var(--ink); }
.oKicker { margin: 0; font-size: 11px; letter-spacing: 0.18em; color: var(--ink-faint); }
.oSub { margin: 0; font-size: 12px; line-height: 1.7; color: var(--ink-dim); }
.oTop .btn { margin-left: auto; flex: none; }
.oHint { margin: 8px 0 0; font-size: 12px; line-height: 1.7; color: var(--ink-dim); }
.oErr {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--r-2);
  border: 1px solid var(--wb-danger-a45); background: var(--wb-danger-a16);
  font-size: 12.5px; color: var(--danger-bright);
}
.oOk {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--r-2);
  border: 1px solid var(--wb-tint-45); background: var(--wb-tint-08);
  font-size: 12.5px; color: var(--action);
}

/* ── 概览条 ───────────────────────────────────────────────────────────── */
.oStats { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.oStat {
  flex: 1 1 150px; min-width: 0; padding: 10px 14px;
  border: 1px solid var(--line); border-radius: var(--r-2); background: var(--surface-1);
}
.oStat-k { display: block; font-size: 11.5px; color: var(--ink-faint); }
.oStat-v {
  display: block; margin-top: 2px; font-size: 20px; line-height: 1.25;
  color: var(--ink); font-variant-numeric: tabular-nums;
}
.oStat-v i { margin-left: 2px; font-size: 11.5px; font-style: normal; color: var(--ink-faint); }

/* ── 两栏 ─────────────────────────────────────────────────────────────── */
.oGrid { display: grid; gap: 14px; align-items: start; }
@media (min-width: 901px) {
  .oGrid {
    grid-template-columns: minmax(0, 1fr) 340px;
    grid-template-areas:
      'queue backfill'
      'ledger backfill'
      'pay backfill';
  }
  .oQueue { grid-area: queue; }
  .oBackfill { grid-area: backfill; position: sticky; top: 12px; }
  .oLedger { grid-area: ledger; }
  .oPay { grid-area: pay; }
}

.oCard {
  padding: 14px; border: 1px solid var(--line);
  border-radius: var(--r-3); background: var(--surface-1);
}
.oCard-hd { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.oCard-hd h2 { margin: 0; font-size: 14px; color: var(--ink); }
.oCount {
  min-width: 20px; padding: 1px 7px; border-radius: var(--r-full); text-align: center;
  font-family: var(--font-mono); font-size: 11.5px; color: var(--action-ink);
  background: var(--warn);
}
.oMonth { margin-left: auto; max-width: 150px; }
/* 月份那一格换成日期组件之后（2026-10-13），`.oMonth` 那两条落在组件的根元素上，
   里面那个 input 的尺寸得自己补一条。 */
.oMonth :deep(.luyun-date-picker .dp--input) { width: 100%; font-size: 13px; }
.oNote { margin-left: auto; font-size: 11.5px; color: var(--ink-faint); }
.oLead { margin: 0 0 10px; font-size: 12px; line-height: 1.7; color: var(--ink-dim); }
.oEmpty { margin: 4px 0 0; font-size: 12.5px; color: var(--ink-faint); }
.oList { list-style: none; margin: 0; padding: 0; }
.oItem { padding: 12px 0; border-top: 1px solid var(--line); }
.oItem:first-child { border-top: 0; padding-top: 6px; }
.oRow-hd { display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap; }
.oRow-hd b { font-size: 14px; color: var(--ink); }
.oDate { font-family: var(--font-mono); font-size: 12.5px; color: var(--ink-dim); }
.oH { font-size: 15px; color: var(--ink); font-variant-numeric: tabular-nums; }
.oH.minus { color: var(--danger-bright); }
.oKind {
  padding: 1px 8px; border-radius: var(--r-full); font-size: 11px;
  color: var(--info); border: 1px solid var(--info); background: var(--info-soft);
}
.oPend { margin-left: auto; font-size: 11.5px; color: var(--warn); }
.oSt { margin-left: auto; font-size: 11.5px; }
.oSt.wait { color: var(--warn); }
.oSt.ok { color: var(--ok); }
.oSt.bad { color: var(--danger-bright); }
.oSt.mute { color: var(--ink-faint); }
.oReason { margin: 6px 0 0; font-size: 13px; line-height: 1.6; color: var(--ink); }
.oBy { margin: 4px 0 0; font-size: 11.5px; color: var(--ink-faint); }
.oAct { display: flex; gap: 8px; margin-top: 10px; }
.oReject { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.oReject .oInput { flex: 1 1 240px; }
.oTotal { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--line); }
.oTotal b { letter-spacing: 0.06em; }

/* ── 表单 ─────────────────────────────────────────────────────────────── */
.oForm { display: flex; flex-direction: column; gap: 10px; }
.oField { display: flex; align-items: center; gap: 10px; }
.oLabel { width: 52px; flex: none; font-size: 12.5px; color: var(--ink-dim); }
.oInput {
  padding: 7px 10px; border: 1px solid var(--line); border-radius: var(--r-2);
  background: transparent; font-size: 13px; color: var(--ink);
}
.oField .oInput { flex: 1; }
/* 日期组件渲染出来的是 `.dp--input`，组件默认给左边的日历图标留了位（别覆盖 padding）。 */
.oField :deep(.luyun-date-picker) { flex: 1; }
.oField :deep(.luyun-date-picker .dp--input) { width: 100%; font-size: 13px; }
.oStepper { display: flex; align-items: center; gap: 10px; }
.oStep {
  width: 32px; height: 32px; border: 1px solid var(--line); border-radius: 50%;
  background: transparent; font-size: 17px; line-height: 1; color: var(--ink);
}
.oHours {
  min-width: 92px; text-align: center; font-size: 15px; color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.oHours.minus { color: var(--danger-bright); }
.oForm > .btn { align-self: flex-start; }
</style>
