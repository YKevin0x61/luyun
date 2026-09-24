<script setup>
/**
 * 员工手机端的「今天」页（票 05）。
 *
 * 原型：`.scratch/scheduling/prototype/today-variants.html` 的 A · 两块 —— 用户选的
 * 「登录后一个入口，上下两块，各挂各的系统牌子」。这一票只有上面那块（排班给的
 * 「今天上不上班」）：下面那块卫生待办卡下一张票接，这会儿留一条去老入口的路，
 * 不然员工登录后只剩这一张卡，卫生的活就没地方看了。
 *
 * 数据只有两个来源，都只认员工自己的 cookie（接口上没有 `employee_id` 可填）：
 * `GET /api/scheduling/me`（今天往后几天的班）与票 08 加的
 * `GET|POST|DELETE /api/scheduling/me/requests`（自己提的请假）。整屏没有钟点 ——
 * 班次没有起止时刻，钟点只在卫生那边。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { staffRequest } from '../../utils/hygieneStaff'
import { canCancel, requestLine } from '../../utils/leaveRequest'
import {
  dayLabel,
  nextTwoLine,
  shiftText,
  shiftTone,
  todayHeadline,
  todaySubline,
  todayTone,
} from '../../utils/todayShift'

useScopedStylesheet('/hygiene-admin.css')

const router = useRouter()
const state = ref('loading') // loading | ready | error
const employee = ref(null)
const days = ref([])
const errorText = ref('')
const note = ref('')

// 请假（票 08）：一张表单 + 一串自己的申请。表单里那两天**默认取服务端的营业日**
// （`days[0].business_date`），不取手机上的今天 —— 手机时区可能不在东八区，
// 而「过去的日子请不了假」是服务端按营业日判的。
const sheet = ref(false)
const leaveStart = ref('')
const leaveEnd = ref('')
const leaveNote = ref('')
const leaveError = ref('')
const leaveBusy = ref(false)
const requests = ref([])
const requestsError = ref('')


const today = computed(() => days.value[0] || null)
const after = computed(() => days.value.slice(1, 4))

// 「休 / 还没排 / 班次被删」的判据在 utils/todayShift.js（那儿有真单测），
// 这一页只负责摆版式，不再重写一遍。
const todayText = computed(() => todayHeadline(today.value))
const tone = computed(() => todayTone(today.value))
const todaySub = computed(() => todaySubline(today.value))
const nextLine = computed(() => nextTwoLine(after.value))

const ENTRIES = [
  { key: 'leave', label: '请假' },
  { key: 'swap', label: '换班' },
  // 整月有地方可去了（票 06）；请假（票 08）在下面打开一张表单；换班等第 9 张票。
  { key: 'month', label: '整月', to: '/today/month' },
]

// 会话没了（换了手机、店里停了账号、cookie 过期）：回员工登录，回来还是这一页。
// 跟卫生首页同一个走法（`leaveForStaffLogin`）：把当前地址整个带过去，
// 员工只有一套登录，登录页也是同一个。
function leaveForStaffLogin() {
  router.replace({
    path: '/hygiene/login',
    query: { next: router.currentRoute.value.fullPath },
  })
}

function open(entry) {
  if (entry.to) {
    router.push(entry.to)
    return
  }
  if (entry.key === 'leave') {
    openLeave()
    return
  }
  note.value = `「${entry.label}」还没开放`
}

function openLeave() {
  leaveError.value = ''
  leaveStart.value = (today.value && today.value.business_date) || ''
  leaveEnd.value = ''
  leaveNote.value = ''
  sheet.value = true
  loadRequests()
}

async function load() {
  state.value = 'loading'
  try {
    const data = await staffRequest('/api/scheduling/me')
    employee.value = data.employee
    days.value = data.days || []
    state.value = 'ready'
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    errorText.value = err.message || '读不到你的班'
    state.value = 'error'
  }
}

/** 读自己提过的请假。
 *
 *  `quiet` 是进页面那一次用的：员工只是来看今天上不上班，请假列表读不出来
 *  （比如管理员还没应用 0008）不该在首页顶一行红字 —— 真要提的时候表单里会说。
 */
async function loadRequests(quiet = false) {
  try {
    const data = await staffRequest('/api/scheduling/me/requests')
    requests.value = data.requests || []
    requestsError.value = ''
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    if (!quiet) requestsError.value = err.message || '你的申请读不出来'
  }
}

async function submitLeave() {
  if (leaveBusy.value) return
  leaveBusy.value = true
  leaveError.value = ''
  try {
    await staffRequest('/api/scheduling/me/requests', {
      method: 'POST',
      body: {
        start_date: leaveStart.value,
        // 只请一天时后一个空着：服务端把 `None` 与空串都当单日，但请求里少一个
        // 字段更好读（日志里一眼看得出这是单日申请）。
        end_date: leaveEnd.value || null,
        note: leaveNote.value || null,
      },
    })
    sheet.value = false
    note.value = '请假提上去了，等店长批'
    await loadRequests()
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    leaveError.value = err.message || '没提交成'
  } finally {
    leaveBusy.value = false
  }
}

async function cancelLeave(request) {
  if (leaveBusy.value) return
  leaveBusy.value = true
  requestsError.value = ''
  try {
    await staffRequest(`/api/scheduling/me/requests/${request.id}`, { method: 'DELETE' })
    note.value = '撤回了'
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    requestsError.value = err.message || '没撤成'
  } finally {
    leaveBusy.value = false
    // 撤没撤成都要重读一次：店长可能刚批了/驳了（那这条就撤不动了），
    // 页面上留一条已经不存在的申请比报错更糟。
    await loadRequests(true)
  }
}

onMounted(() => {
  document.title = '今天'
  load()
  // 顺手读一次自己的请假：有等着批的时候，首页就能看见（读不出来不吭声）。
  loadRequests(true)
})
</script>

<template>
  <div class="today-page hygiene-staff">
    <header class="tTop">
      <span class="tDay">今天</span>
      <span class="tDate">{{ today ? dayLabel(today.business_date) : '' }}</span>
      <span v-if="employee" class="tMe">{{ employee.name }}</span>
    </header>

    <div class="tA-body">
      <p v-if="state === 'loading'" class="tA-sub">正在读你的班…</p>

      <template v-else-if="state === 'error'">
        <p class="tA-sub">{{ errorText }}</p>
        <button class="btn btn-block" type="button" @click="load">重试</button>
      </template>

      <template v-else>
        <section class="tA-card sched">
          <div class="tA-hd">
            <span class="tag sched">排班</span>
            <em>{{ nextLine }}</em>
          </div>
          <p class="shift" :class="tone">{{ todayText }}</p>
          <p class="tA-sub">{{ todaySub }}</p>
          <div class="acts">
            <button
              v-for="entry in ENTRIES"
              :key="entry.key"
              class="btn"
              type="button"
              @click="open(entry)"
            >
              {{ entry.label }}
            </button>
          </div>
          <p v-if="note" class="tA-note">{{ note }}</p>
        </section>

        <p class="tA-h">往后三天</p>
        <div class="next3">
          <div v-for="day in after" :key="day.business_date">
            <span class="d">{{ dayLabel(day.business_date) }}</span>
            <span class="s" :class="shiftTone(day)">{{ shiftText(day) }}</span>
          </div>
        </div>

        <!-- 自己的请假（票 08）：只在提过或读出错时才占地方。 -->
        <section v-if="requests.length || requestsError" class="tA-card leave">
          <div class="tA-hd">
            <span class="tag leave">请假</span>
            <em>{{ requests.length }} 条</em>
          </div>
          <p v-if="requestsError" class="tA-sub">{{ requestsError }}</p>
          <ul class="tL-list">
            <li v-for="request in requests" :key="request.id">
              <span class="tL-line">{{ requestLine(request) }}</span>
              <button
                v-if="canCancel(request)"
                class="btn tL-cancel"
                type="button"
                :disabled="leaveBusy"
                @click="cancelLeave(request)"
              >撤回</button>
            </li>
          </ul>
          <p class="tA-sub">批了才算请假：批完那天在班表上写「请假」。</p>
        </section>

        <!-- 卫生那块（原型 A 的下半张卡）下一张票接上。 -->
        <section class="tA-card hyg">
          <div class="tA-hd">
            <span class="tag hyg">卫生</span>
            <em>下一张票接上</em>
          </div>
          <p class="tA-sub">卫生待办卡还没接上，先用老入口。</p>
          <div class="acts">
            <button class="btn" type="button" @click="router.push('/hygiene')">
              去卫生待办 ›
            </button>
          </div>
        </section>

        <p class="tA-foot">
          排班只说班次，不说几点上班 —— 钟点只有卫生那边才有（逾期点）。
        </p>
      </template>
    </div>

    <!-- 请假表单（票 08）。容器用主题里那套 `.modal-overlay/.modal-box`：员工端所有
         页都加载了 theme.css，卫生那几页的确认框也是这么弹的。 -->
    <div
      v-if="sheet"
      class="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="leave-sheet-title"
      @click.self="sheet = false"
    >
      <div class="modal-box">
        <div class="modal-header">
          <h3 id="leave-sheet-title">请假</h3>
          <button class="btn" type="button" @click="sheet = false">关闭</button>
        </div>
        <p class="tA-sub">
          请哪几天：只请一天就把后一格空着。今天起才能请，最多请到排班铺到的那天。
        </p>
        <div class="form-row">
          <label for="leave-start">从哪天</label>
          <input id="leave-start" v-model="leaveStart" class="input" type="date">
        </div>
        <div class="form-row">
          <label for="leave-end">到哪天（可空）</label>
          <input id="leave-end" v-model="leaveEnd" class="input" type="date">
        </div>
        <div class="form-row">
          <label for="leave-note">事由（可空，最多 50 字）</label>
          <input
            id="leave-note"
            v-model="leaveNote"
            class="input"
            type="text"
            maxlength="50"
            placeholder="例如：家里有事"
          >
        </div>
        <p v-if="leaveError" class="tL-err" role="alert">{{ leaveError }}</p>
        <div class="modal-footer">
          <button class="btn" type="button" @click="sheet = false">取消</button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="leaveBusy || !leaveStart"
            @click="submitLeave"
          >提交</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 原型 A 的骨架：整屏 → 排班卡 → 往后三天 →（卫生卡）→ 页脚。
   令牌与 .btn 的底座来自 public/hygiene-admin.css（员工端作用域 .hygiene-staff）。 */
.today-page {
  min-height: 100vh;
  background:
    radial-gradient(130% 46% at 50% -6%, rgba(63, 224, 176, .1), transparent 62%),
    var(--hy-bg);
  padding-bottom: 80px;
}

.tTop {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 18px 16px 14px;
}

.tDay {
  font-family: var(--font-song);
  font-size: 20px;
  letter-spacing: .16em;
  font-weight: 600;
}

.tDate {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--hy-faint);
}

.tMe {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--hy-muted);
  border: 1px solid var(--hy-line);
  border-radius: 999px;
  padding: 4px 10px;
  background: var(--hy-surface-2);
}

.tA-body {
  padding: 0 16px 26px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.tA-card {
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-lg);
  padding: 14px 15px;
  background: var(--hy-surface-2);
}

.tA-card.sched {
  border-color: var(--hy-mint-line);
  background: linear-gradient(168deg, rgba(63, 224, 176, .09), transparent 58%),
    var(--hy-surface-2);
}

/* 这张样式表里没有 --hy-aqua-line/--hy-aqua-soft，用同色 rgba 顶上。 */
.tA-card.hyg {
  border-color: rgba(95, 214, 230, .36);
  background: linear-gradient(168deg, rgba(95, 214, 230, .07), transparent 58%),
    var(--hy-surface-2);
}

/* 请假那张卡（票 08）跟卫生卡同一个色系，但自己的事自己一眼认出来。 */
.tA-card.leave {
  border-color: var(--hy-aqua);
  background: linear-gradient(168deg, rgba(94, 234, 212, .07), transparent 58%),
    var(--hy-surface-2);
}

.tA-hd {
  display: flex;
  align-items: center;
  gap: 9px;
}

.tA-hd em {
  margin-left: auto;
  font-style: normal;
  font-size: 10.5px;
  color: var(--hy-faint);
}

.tag {
  font-size: 10px;
  letter-spacing: .12em;
  padding: 2.5px 7px;
  border-radius: 999px;
  border: 1px solid transparent;
}

.tag.sched {
  color: var(--hy-mint);
  background: var(--hy-mint-soft);
  border-color: var(--hy-mint-line);
}

.tag.hyg {
  color: var(--hy-aqua);
  background: rgba(95, 214, 230, .12);
  border-color: rgba(95, 214, 230, .36);
}

/* 请假（票 08）：跟自己提的那件事同色。 */
.tag.leave {
  color: var(--hy-aqua);
  background: rgba(94, 234, 212, .12);
  border-color: var(--hy-aqua);
}

.shift {
  margin: 0;
  font-family: var(--font-song);
  font-size: 52px;
  line-height: 1.08;
  letter-spacing: .08em;
  color: var(--hy-jade);
  padding: 6px 0 2px;
}

.shift.rest {
  color: var(--hy-faint);
}

/* 行上那个班次已经没了（票 11 管删除）：跟「休」分开说、分开上色。 */
.shift.moved {
  font-size: 24px;
  line-height: 1.4;
  color: var(--hy-amber);
}

/* 「今天没有你的班」是一句话不是两个大字：这句按正文大小排，不然会顶出屏幕。 */
.shift.none {
  font-size: 20px;
  line-height: 1.5;
  letter-spacing: .04em;
  color: var(--hy-muted);
  padding: 16px 0 10px;
}

/* 请假（票 08）：批过的假。跟「休」分开说、分开上色 —— 休是排班给的，
   假是自己提的、店长批的。字号也降一档：它不是一个「班次」。 */
.shift.leave {
  font-size: 34px;
  line-height: 1.3;
  letter-spacing: .06em;
  color: var(--hy-aqua);
}

.tA-sub {
  margin: 0;
  font-size: 11.5px;
  color: var(--hy-muted);
}

.acts {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}

.acts .btn {
  flex: 1;
}

.tA-note {
  margin: 9px 0 0;
  font-size: 11px;
  color: var(--hy-amber);
}

.tA-h {
  margin: 0;
  padding: 2px 3px 0;
  font-family: var(--font-song);
  font-size: 12.5px;
  letter-spacing: .14em;
  color: var(--hy-muted);
}

.next3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.next3 > div {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 10px 6px;
  text-align: center;
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-md);
  background: var(--hy-surface-2);
}

.next3 .d {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--hy-faint);
}

.next3 .s {
  font-family: var(--font-song);
  font-size: 17px;
  letter-spacing: .08em;
  color: var(--hy-jade);
}

.next3 .s.r,
.next3 .s.rest {
  color: var(--hy-faint);
}

/* 班次被删了（票 11 能删班次）：那天的班次没了，不等于那天不上班 —— 不要跟「休」同色。 */
.next3 .s.moved {
  color: var(--hy-amber);
  font-size: 13px;
}

.next3 .s.none {
  color: var(--hy-faint);
  opacity: .72;
}

/* 请假（票 08）：三格里也认得出是假不是休。 */
.next3 .s.leave {
  color: var(--hy-aqua);
  font-size: 14px;
}

/* 自己的申请（票 08）：一条一行，撤回按钮贴右边。 */
.tL-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
}

.tL-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 0;
  border-top: 1px solid var(--hy-line);
}

.tL-list li:first-child {
  border-top: 0;
}

.tL-line {
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--hy-ink);
}

.tL-cancel {
  margin-left: auto;
  flex: none;
  min-height: 32px;
  padding: 0 12px;
  font-size: 12px;
}

.tL-err {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--hy-coral);
}

.tA-foot {
  margin: 0;
  padding: 2px 4px;
  font-size: 10.5px;
  color: var(--hy-faint);
  line-height: 1.75;
}
</style>
