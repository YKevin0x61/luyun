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
import { useNudgePull } from '../../composables/useNudgePull'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { loginRedirectTarget } from '../../utils/loginNext'
import { staffRequest } from '../../utils/hygieneStaff'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
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

useScopedStylesheet('/hygiene-admin.css')

const router = useRouter()
const state = ref('loading') // loading | ready | error
const employee = ref(null)
const today = ref('')
const yesterday = ref('')
const entries = ref([])
const summary = ref(null)
const errorText = ref('')

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

async function load(quiet = false) {
  if (!quiet) state.value = 'loading'
  try {
    const data = await staffRequest('/api/overtime/me')
    employee.value = data.employee
    today.value = data.today
    yesterday.value = data.yesterday
    entries.value = data.entries || []
    summary.value = data.summary || null
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
      <span class="tDay">加班与补钟</span>
      <span class="tDate">{{ employee ? employee.name : '' }}</span>
    </header>

    <p v-if="state === 'loading'" class="mSub">正在读你的加班记录…</p>

    <template v-else-if="state === 'error'">
      <p class="mSub">{{ errorText }}</p>
      <button class="btn btn-block" type="button" @click="load()">重试</button>
    </template>

    <template v-else>
      <div v-if="summary" class="oCard">
        <div class="oCardHd">{{ summary.month }} 这个月</div>
        <div class="oCardRow">
          <span>已批准</span>
          <b>{{ formatHalfHours(summary.approved.net_half_hours) }} 小时</b>
        </div>
        <div class="oCardRow">
          <span>待审批</span>
          <b>{{ formatHalfHours(summary.pending.net_half_hours) }} 小时</b>
        </div>
        <p class="oHint">只有「已批准」那一行是算数的；待审批的还可能被驳回。</p>
      </div>

      <!-- 店长那一面（票 04）：打开页面第一件事是「有什么等着我批」，所以它在登记
           表单前面。没有这个能力的人根本看不到这一块（服务端 403 兜底）。 -->
      <div v-if="canReview" class="oList oReview">
        <div class="oListHd">等着我批的（{{ pending.length }}）</div>
        <p v-if="reviewError" class="oErr">{{ reviewError }}</p>
        <div v-for="entry in pending" :key="entry.id" class="oItem">
          <div class="oL1">
            <span class="oDate">{{ entry.entry_date }}</span>
            <span class="oH" :class="{ minus: entry.half_hours < 0 }">
              {{ formatHalfHours(entry.half_hours) }} 小时
            </span>
            <span class="oSt wait">{{ entry.employee_name }}</span>
          </div>
          <p class="oReason">{{ entry.reason }}</p>
          <p class="oWho">{{ submittedByText(entry) }}</p>
          <div class="oBtns">
            <button
              class="oPill ok"
              type="button"
              :disabled="reviewBusyId === entry.id"
              @click="decide(entry, 'approve')"
            >
              批准
            </button>
            <button
              class="oPill bad"
              type="button"
              :disabled="reviewBusyId === entry.id"
              @click="startReject(entry)"
            >
              驳回
            </button>
          </div>
          <div v-if="rejectingId === entry.id" class="oRejectBox">
            <input
              v-model="rejectReason"
              class="oInput"
              type="text"
              :maxlength="maxRejectReason"
              placeholder="为什么不算（必填，员工看得到）"
            />
            <button
              class="oPill bad"
              type="button"
              :disabled="!rejectReason.trim() || reviewBusyId === entry.id"
              @click="decide(entry, 'reject', { reason: rejectReason.trim() })"
            >
              确认驳回
            </button>
          </div>
        </div>
        <p v-if="!pending.length && !reviewError" class="mSub">没有等着你批的登记。</p>
      </div>

      <form class="oForm" @submit.prevent="submit">
        <!-- 店长能补录任意过去日期，所以给他一个日期输入；员工只有今天 / 昨天两个
             选项，那两个按钮本身就是「窗口」的可视化。 -->
        <div v-if="canReview" class="oRow">
          <span class="oLabel">哪一天</span>
          <input v-model="day" class="oInput" type="date" :max="today" />
        </div>
        <div v-else class="oRow">
          <span class="oLabel">哪一天</span>
          <div class="oDays">
            <button
              v-for="opt in dayOptions"
              :key="opt.value"
              class="oDay"
              :class="{ on: day === opt.value }"
              type="button"
              @click="day = opt.value"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>

        <div v-if="canReview && roster.length" class="oRow">
          <span class="oLabel">替谁记</span>
          <select v-model="backfillFor" class="oInput">
            <option value="">我自己</option>
            <option v-for="person in roster" :key="person.id" :value="String(person.id)">
              {{ person.name }}{{ person.disabled ? '（已停用）' : '' }}
            </option>
          </select>
        </div>

        <div class="oRow">
          <span class="oLabel">时长</span>
          <div class="oStepper">
            <button
              class="oStep"
              type="button"
              aria-label="减半小时"
              @click="halfHours = stepHalfHours(halfHours, -1, maxHalfHours)"
            >
              −
            </button>
            <span class="oHours" :class="{ minus: halfHours < 0 }">{{ hoursText }}</span>
            <button
              class="oStep"
              type="button"
              aria-label="加半小时"
              @click="halfHours = stepHalfHours(halfHours, 1, maxHalfHours)"
            >
              +
            </button>
            <span class="oUnit">小时</span>
          </div>
        </div>

        <div class="oRow">
          <span class="oLabel">事由</span>
          <input
            v-model="reason"
            class="oInput"
            type="text"
            :maxlength="maxReason"
            placeholder="这笔是干什么的（必填）"
          />
        </div>

        <p v-if="formError" class="oErr">{{ formError }}</p>
        <p v-else-if="note" class="oNote">{{ note }}</p>
        <button class="btn btn-block" type="submit" :disabled="!canSubmit">
          {{ busy ? '提交中…' : '提交登记' }}
        </button>
        <p class="oHint">
          加班记正数、补钟记负数，1 格 = 0.5 小时，单笔最多 {{ maxHalfHours / 2 }} 小时。
          只能登记今天与昨天，更早的请找店长补录。
        </p>
      </form>

      <div class="oList">
        <div class="oListHd">我的记录</div>
        <div v-for="entry in entries" :key="entry.id" class="oItem">
          <div class="oL1">
            <span class="oDate">{{ entry.entry_date }}</span>
            <span class="oH" :class="{ minus: entry.half_hours < 0 }">
              {{ formatHalfHours(entry.half_hours) }} 小时
            </span>
            <span class="oSt" :class="entryStatusTone(entry.status)">
              {{ entryStatusText(entry.status) }}
            </span>
          </div>
          <p class="oReason">{{ entry.reason }}</p>
          <p v-if="entry.reject_reason" class="oErr">驳回理由：{{ entry.reject_reason }}</p>
          <button
            v-if="entry.status === 'pending'"
            class="oCancel"
            type="button"
            :disabled="cancelBusyId === entry.id"
            @click="cancelEntry(entry)"
          >
            {{ cancelBusyId === entry.id ? '撤回中…' : '撤回' }}
          </button>
        </div>
        <p v-if="!entries.length" class="mSub">
          还没有登记过。加班记一笔、欠钟记一笔，都会出现在这里。
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.overtime-page {
  padding: 12px 14px 28px;
}
.oCard {
  border: 1px solid var(--hy-line, #e3e3e3);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.oCardHd {
  font-size: 13px;
  letter-spacing: 0.06em;
  color: var(--hy-ink, #222);
  margin-bottom: 8px;
}
.oCardRow {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
  padding: 3px 0;
}
.oForm {
  border: 1px solid var(--hy-line, #e3e3e3);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 16px;
}
.oRow {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.oLabel {
  width: 52px;
  flex: none;
  font-size: 13px;
  color: var(--hy-mute, #888);
}
.oDays {
  display: flex;
  gap: 8px;
}
.oDay {
  padding: 6px 16px;
  border: 1px solid var(--hy-line, #e3e3e3);
  border-radius: 999px;
  background: transparent;
  font-size: 14px;
}
.oDay.on {
  border-color: var(--hy-mint, #2f9e7f);
  color: var(--hy-mint, #2f9e7f);
}
.oStepper {
  display: flex;
  align-items: center;
  gap: 10px;
}
.oStep {
  width: 34px;
  height: 34px;
  border: 1px solid var(--hy-line, #e3e3e3);
  border-radius: 50%;
  background: transparent;
  font-size: 18px;
  line-height: 1;
}
.oHours {
  min-width: 66px;
  text-align: center;
  font-size: 19px;
  font-variant-numeric: tabular-nums;
}
.oHours.minus {
  color: #c0392b;
}
.oUnit {
  font-size: 12px;
  color: var(--hy-mute, #888);
}
.oInput {
  flex: 1;
  padding: 8px 10px;
  border: 1px solid var(--hy-line, #e3e3e3);
  border-radius: 8px;
  font-size: 14px;
}
.oErr {
  font-size: 13px;
  color: #c0392b;
  margin: 4px 0 8px;
}
.oNote {
  font-size: 13px;
  color: var(--hy-mint, #2f9e7f);
  margin: 4px 0 8px;
}
.oHint {
  font-size: 12px;
  color: var(--hy-mute, #888);
  line-height: 1.6;
  margin-top: 8px;
}
.oListHd {
  font-size: 13px;
  letter-spacing: 0.06em;
  color: var(--hy-ink, #222);
  margin-bottom: 8px;
}
.oItem {
  border-top: 1px solid var(--hy-line, #eee);
  padding: 10px 0;
}
.oL1 {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.oDate {
  font-size: 13px;
  color: var(--hy-mute, #888);
  font-variant-numeric: tabular-nums;
}
.oH {
  font-size: 16px;
  font-variant-numeric: tabular-nums;
}
.oH.minus {
  color: #c0392b;
}
.oSt {
  margin-left: auto;
  font-size: 12px;
}
.oSt.wait {
  color: #b8860b;
}
.oSt.ok {
  color: var(--hy-mint, #2f9e7f);
}
.oSt.bad {
  color: #c0392b;
}
.oSt.mute {
  color: var(--hy-mute, #999);
}
.oReason {
  font-size: 14px;
  margin: 4px 0 0;
}
.oCancel {
  margin-top: 6px;
  padding: 4px 12px;
  border: 1px solid var(--hy-line, #e3e3e3);
  border-radius: 999px;
  background: transparent;
  font-size: 13px;
}

/* 店长那一面（票 04）：待审批队列、批 / 驳、代录选人。同一页多一块，所以给它自己的
   边框 —— 两件事（我提的 / 我判的）在视觉上不能混成一串。 */
.oReview {
  border: 1px solid var(--hy-line, #e3e3e3);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 16px;
}
.oWho {
  font-size: 12px;
  color: var(--hy-mute, #888);
  margin: 2px 0 0;
}
.oBtns {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.oPill {
  padding: 5px 14px;
  border: 1px solid var(--hy-line, #e3e3e3);
  border-radius: 999px;
  background: transparent;
  font-size: 13px;
}
.oPill.ok {
  border-color: var(--hy-mint, #2f9e7f);
  color: var(--hy-mint, #2f9e7f);
}
.oPill.bad {
  border-color: #c0392b;
  color: #c0392b;
}
.oPill:disabled {
  opacity: 0.5;
}
.oRejectBox {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
}
.oRejectBox .oInput {
  min-width: 0;
}
</style>
