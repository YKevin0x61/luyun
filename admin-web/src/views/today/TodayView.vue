<script setup>
/**
 * 员工手机端的「今天」页（票 05）。
 *
 * 原型：`.scratch/scheduling/prototype/today-variants.html` 的 A · 两块 —— 用户选的
 * 「登录后一个入口，上下两块，各挂各的系统牌子」。这一票只有上面那块（排班给的
 * 「今天上不上班」）：下面那块卫生待办卡下一张票接，这会儿留一条去老入口的路，
 * 不然员工登录后只剩这一张卡，卫生的活就没地方看了。
 *
 * 数据只有三个来源，都只认员工自己的 cookie（接口上没有 `employee_id` 可填）：
 * `GET /api/scheduling/me`（今天往后几天的班）、票 08 加的
 * `GET|POST|DELETE /api/scheduling/me/requests`（自己提的请假）与票 09 加的
 * `GET /api/scheduling/me/colleagues` + `POST /api/scheduling/me/swaps*`（换班：
 * 提一条、替对方点头或摇头）。整屏没有钟点 —— 班次没有起止时刻，钟点只在卫生那边。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { staffRequest } from '../../utils/hygieneStaff'
import { canCancel, incomingLine, requestLine } from '../../utils/leaveRequest'
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

// 换班（票 09）：自己提的那些跟请假在同一张卡里（`requests` 按 `kind` 分流），
// 这里多两块 —— 提名同事的一张表单，以及「别人问我换班」的待回应清单。
const swapSheet = ref(false)
const colleagues = ref([])
const swapPeer = ref('')
const swapDay = ref('')
const swapNote = ref('')
const swapError = ref('')
const swapBusy = ref(false)
const incoming = ref([])


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
  // 整月有地方可去了（票 06）；请假（票 08）与换班（票 09）都在下面打开一张表单。
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
  if (entry.key === 'swap') {
    openSwap()
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

/** 换班表单（票 09）：选一位同事、选一天。日期同样默认取服务端的营业日。 */
function openSwap() {
  swapError.value = ''
  swapPeer.value = ''
  swapDay.value = (today.value && today.value.business_date) || ''
  swapNote.value = ''
  swapSheet.value = true
  loadColleagues()
}

/** 能跟谁换：服务端只回在上班的别人（停用/未批准的已经滤掉），这里不再筛一遍。 */
async function loadColleagues() {
  try {
    const data = await staffRequest('/api/scheduling/me/colleagues')
    colleagues.value = data.colleagues || []
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    colleagues.value = []
    swapError.value = err.message || '同事名单读不出来'
  }
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

/** 读自己提过的申请（请假 + 换班）与「别人问我换班」的那几条。
 *
 *  `quiet` 是进页面那一次用的：员工只是来看今天上不上班，申请列表读不出来
 *  （比如管理员还没应用 0008 / 0009）不该在首页顶一行红字 —— 真要提的时候表单里会说。
 */
async function loadRequests(quiet = false) {
  try {
    const data = await staffRequest('/api/scheduling/me/requests')
    requests.value = data.requests || []
    incoming.value = data.incoming || []
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

async function submitSwap() {
  if (swapBusy.value) return
  swapBusy.value = true
  swapError.value = ''
  try {
    await staffRequest('/api/scheduling/me/swaps', {
      method: 'POST',
      body: {
        peer_employee_id: Number(swapPeer.value) || null,
        business_date: swapDay.value,
        note: swapNote.value || null,
      },
    })
    swapSheet.value = false
    note.value = '换班提上去了，等对方同意'
    await loadRequests()
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    swapError.value = err.message || '没提交成'
  } finally {
    swapBusy.value = false
  }
}

/** 别人问我换班：同意 / 拒绝（票 09 的验收 2）。
 *
 *  同意之后这条才进店长待办；拒绝就到此为止 —— 两种走法都由服务端说了算，
 *  这一页只把结果说回给人听，然后重读一遍（对方可能自己撤了）。
 */
async function answerSwap(card, agree) {
  if (swapBusy.value) return
  swapBusy.value = true
  requestsError.value = ''
  try {
    await staffRequest(`/api/scheduling/me/swaps/${card.id}/${agree ? 'accept' : 'reject'}`, {
      method: 'POST',
    })
    note.value = agree ? '你同意了，接下来等店长批' : '你拒绝了，这件事到此为止'
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    requestsError.value = err.message || '没回成'
  } finally {
    swapBusy.value = false
    await loadRequests(true)
  }
}

onMounted(() => {
  document.title = '今天'
  load()
  // 顺手读一次自己的申请：有等着批的、或别人问我换班的，首页就能看见（读不出来不吭声）。
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

        <!-- 别人问我换班（票 09）：他还没等到我点头，店长那边看不见这条。 -->
        <section v-if="incoming.length" class="tA-card swap">
          <div class="tA-hd">
            <span class="tag swap">换班</span>
            <em>{{ incoming.length }} 条等我回应</em>
          </div>
          <ul class="tL-list">
            <li v-for="card in incoming" :key="card.id">
              <span class="tL-line">{{ incomingLine(card) }}</span>
              <span class="tL-acts">
                <button
                  class="btn tL-cancel"
                  type="button"
                  :disabled="swapBusy"
                  @click="answerSwap(card, true)"
                >同意</button>
                <button
                  class="btn tL-cancel"
                  type="button"
                  :disabled="swapBusy"
                  @click="answerSwap(card, false)"
                >拒绝</button>
              </span>
              <span v-if="card.note" class="tL-note">事由：{{ card.note }}</span>
            </li>
          </ul>
          <p class="tA-sub">你点了同意才会轮到店长批；不点，这件事就停在你这里。</p>
        </section>

        <!-- 我自己提的申请（票 08 请假、票 09 换班）：只在提过或读出错时才占地方。 -->
        <section v-if="requests.length || requestsError" class="tA-card leave">
          <div class="tA-hd">
            <span class="tag leave">我的申请</span>
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
          <p class="tA-sub">
            请假批了那天写「请假」；换班要对方先同意、店长再批，批完两个人才对调。
          </p>
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
    <!-- 换班表单（票 09）：选一位同事 + 选一天。跟请假同一个弹层底子。 -->
    <div
      v-if="swapSheet"
      class="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="swap-sheet-title"
      @click.self="swapSheet = false"
    >
      <div class="modal-box">
        <div class="modal-header">
          <h3 id="swap-sheet-title">换班</h3>
          <button class="btn" type="button" @click="swapSheet = false">关闭</button>
        </div>
        <p class="tA-sub">
          跟谁换、换哪一天：对方在手机上点同意，店长才看得到这条；两边都点头之后才轮到店长批。
        </p>
        <div class="form-row">
          <label for="swap-peer">跟谁换</label>
          <select id="swap-peer" v-model="swapPeer" class="input">
            <option value="">选一位同事</option>
            <option v-for="person in colleagues" :key="person.id" :value="person.id">
              {{ person.name }}<template v-if="person.job_title"> · {{ person.job_title }}</template>
            </option>
          </select>
        </div>
        <div class="form-row">
          <label for="swap-day">换哪天</label>
          <input id="swap-day" v-model="swapDay" class="input" type="date">
        </div>
        <div class="form-row">
          <label for="swap-note">事由（可空，最多 50 字）</label>
          <input
            id="swap-note"
            v-model="swapNote"
            class="input"
            type="text"
            maxlength="50"
            placeholder="例如：那天有事，想跟他换个班"
          >
        </div>
        <p v-if="swapError" class="tL-err" role="alert">{{ swapError }}</p>
        <div class="modal-footer">
          <button class="btn" type="button" @click="swapSheet = false">取消</button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="swapBusy || !swapPeer || !swapDay"
            @click="submitSwap"
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

/* 别人问我换班（票 09）：暖色 —— 跟「我自己提的」那张冷色的分开，
   手机上一眼看出这条是等我动手的。 */
.tA-card.swap {
  border-color: var(--hy-amber);
  background: linear-gradient(168deg, rgba(245, 196, 81, .08), transparent 58%),
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

/* 换班（票 09）：跟「等我回应」那张卡同一个暖色。 */
.tag.swap {
  color: var(--hy-amber);
  background: rgba(245, 196, 81, .12);
  border-color: var(--hy-amber);
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

/* 换班那条的两个按钮（同意 / 拒绝）挨在一起，不各自贴右边。 */
.tL-acts {
  margin-left: auto;
  flex: none;
  display: flex;
  gap: 6px;
}

.tL-acts .tL-cancel {
  margin-left: 0;
}

/* 换班那条的事由：小一号，跟在那一行下面。 */
.tL-note {
  flex-basis: 100%;
  font-size: 11px;
  line-height: 1.7;
  color: var(--hy-muted);
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
