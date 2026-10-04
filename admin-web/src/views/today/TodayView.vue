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
import StaffExitButton from '../../components/staff/StaffExitButton.vue'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import { useNudgePull } from '../../composables/useNudgePull'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
import { staffRequest } from '../../utils/hygieneStaff'
import { buildWorkQueue, dailyProgress, shiftClock } from '../../utils/hygieneWorkFlow'
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
// 进页面那次申请列表没读出来（`loadRequests(true)`）：换班那张卡是「有才显示」的，
// 读失败时它整块不在，员工只会以为没人找他换班、也没人等他回应。这一位就是给那种
// 情况留的一句话 + 一个重试按钮（页面上没别的重来路径，只有整页刷新）。
const requestsUnread = ref(false)
// 事由的长度上限由服务端给（`/me/requests` 的 `max_request_note`）：以前这里写死
// 「最多 50 字」+ `maxlength="50"`，服务端一改就两边不一致（别的上限都是随响应下的）。
const maxNote = ref(null)
const noteLabel = computed(
  () => `事由（可空${maxNote.value ? `，最多 ${maxNote.value} 字` : ''}）`,
)

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

// 卫生那块（票 10）：今天的日常检查进度与逾期点，跟上面那张排班卡**各拉各的** ——
// 一块读不出来，另一块照常显示（票 10 的验收：两块卡各自渲染）。
// 只摊数字、不摊"下一项该拍哪个"：真正的队列（含"这张图还在传"的状态）在卫生那一屏，
// 这里要是也列一份，员工会对着正在上传的项再拍一次。
const hygiene = ref({
  state: 'loading', // loading | ready | error
  error: '',
  items: [],
  clocks: null,
  shift: null,
  zoneName: '',
  now: 0,
})

const hygieneStats = computed(() => dailyProgress(hygiene.value.items))
const hygieneDue = computed(() => shiftClock(hygiene.value.shift, hygiene.value.clocks))
const hygieneOverdue = computed(() => buildWorkQueue({
  inbox: hygiene.value.items,
  shiftDue: hygieneDue.value,
  now: hygiene.value.now,
}).filter((task) => task.bucket === 'overdue').length)

// 仪容仪表（票 12）：按**人**拍，今天排到班次才要拍 —— 休假的与没排到的连这一行都
// 不显示（`required` 由排班给，不是「谁有账号」）。它不属于工作区那三类日常，所以是
// 这块里单独的一行，**不进** `hygieneStats` 的分子分母。
const attire = ref({
  state: 'loading', // loading | ready | error
  error: '',
  businessDate: '',
  required: false,
  status: 'todo', // todo | pending | passed | rejected
  note: '',
  hasStandard: false,
})
// 拍照是两步（ADR 0050：标准图那一屏与取景器**不能同屏**，也不做分屏）。所以这里存
// 的是「现在哪一屏」而不是「弹层开没开」：standard → camera 是换屏，不是叠上去。
const attireSheet = ref('') // '' | 'standard' | 'camera'
const imageUploads = useImageUploadQueueStore()
// 「这张正在传」从上传队列派生，不用本组件的 ref：刷新页面 / 回收 webview 之后草稿会
// 恢复继续传（跟卫生那一屏同一条口径），局部状态会让员工对着正在传的那张再拍一遍。
const attireKey = computed(() => `attire:${attire.value.businessDate}`)
// 这一行的色调：没标准图（做不了，得找店长）= 琥珀，被驳回（要重拍）= 珊瑚，
// 待验收 = 中性，其余（没拍 / 已通过）= 玉色。分支顺序与模板里那串 v-if 一致 ——
// 「被驳回」先于「没标准图」，两边判据不一致的话颜色和文案会对不上。
const attireTone = computed(() => {
  if (attire.value.status === 'rejected') return 'rejected'
  if (!attire.value.hasStandard) return 'nostandard'
  if (attire.value.status === 'pending') return 'pending'
  return ''
})
const attirePending = computed(() => imageUploads.activeTasks.some(
  (task) => task.pendingKey === attireKey.value,
))

function openAttireStandard() {
  attireSheet.value = 'standard'
}

function openAttireCamera() {
  // 相机只能在有标准图时开：员工要照着它拍（没传标准图时服务端也会拒）。
  if (!attire.value.hasStandard) return
  attireSheet.value = 'camera'
}

/** 拍完就进上传队列（不是直接 fetch）：断网/地铁里拍的那张不丢，恢复后自己传。 */
function onAttireCaptured(blob) {
  attireSheet.value = ''
  try {
    const form = new FormData()
    form.append('file', blob, 'attire.jpg')
    form.append('live', 'true')
    imageUploads.enqueue({
      transport: 'staff',
      path: '/api/hygiene/staff/attire/submit',
      formData: form,
      label: '仪容仪表 · 现场自拍',
      detail: attire.value.businessDate ? dayLabel(attire.value.businessDate) : '',
      pendingKey: attireKey.value,
      onSuccess: () => loadHygiene(true),
      onError: () => { attire.value = { ...attire.value, error: '刚才那张没传上去，可在上传列表里重试' } },
    })
  } catch (err) {
    attire.value = { ...attire.value, error: err.message || '无法加入上传队列' }
  }
}

// 撤回与「拒绝」不可逆（服务端状态机只往前走：撤回要重提、拒绝对对方就是「没同意」），
// 手机上一误触没有回头路 —— 两个都先过确认框。同仓库对不可逆动作一律这么办（店长驳回、
// 删班次、卫生端那几处）。**同意不弹框**：点了之后还有店长那道闸，跟店长端「批准不弹、
// 驳回弹」同一个口径 —— 三个都拦一道就成了每次都要多点一下。
const cancelTarget = ref(null)
const answerTarget = ref(null) // { card, agree }


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
  { key: 'month', label: '整月', to: '/workbench/me/month' },
]

// 会话没了（换了手机、店里停了账号、cookie 过期）：回员工登录，回来还是这一页。
// 票 03 起员工登录页就是 `/login` 的员工栏（`?next=` 落在员工端前缀内时面板会强制
// 开员工栏）；跟卫生首页同一个走法（`leaveForStaffLogin`）：把当前地址整个带过去。
function leaveForStaffLogin() {
  router.replace({
    path: '/login',
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

async function load(quiet = false) {
  // `quiet`：实时 nudge 触发的重读，别把整页打回 loading（会闪一下「正在读你的班…」）。
  if (!quiet) state.value = 'loading'
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
 *  但**不能装作「没有人等你回应」**：立一个 `requestsUnread` 的牌子，页面上留一句
 *  能点重试的话（不然那张换班卡就是静默消失的）。
 */
async function loadRequests(quiet = false) {
  try {
    const data = await staffRequest('/api/scheduling/me/requests')
    requests.value = data.requests || []
    incoming.value = data.incoming || []
    if (data.max_request_note) maxNote.value = data.max_request_note
    requestsError.value = ''
    requestsUnread.value = false
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    if (!quiet) requestsError.value = err.message || '你的申请读不出来'
    if (quiet) requestsUnread.value = true
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
  let failure = ''
  try {
    await staffRequest(`/api/scheduling/me/requests/${request.id}`, { method: 'DELETE' })
    note.value = '撤回了'
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    failure = err.message || '没撤成'
  } finally {
    leaveBusy.value = false
    // 撤没撤成都要重读一次：店长可能刚批了/驳了（那这条就撤不动了），
    // 页面上留一条已经不存在的申请比报错更糟。
    await loadRequests(true)
    // 「没撤成」那句话留到重读**之后**再说：`loadRequests` 读成功时会把 `requestsError`
    // 清空（那是给「读不出来」用的），先说等于当场抹掉 —— 员工点了撤回看到界面什么都
    // 没变，会以为撤回了。服务端那句「已经处理过了」也就一起丢了。
    if (failure) requestsError.value = failure
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
  let failure = ''
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
    failure = err.message || '没回成'
  } finally {
    swapBusy.value = false
    // 回没回成都要重读一次（对方可能自己撤了）；「没回成」那句话留到重读之后再说，
    // 理由同 `cancelLeave`：重读成功会把 `requestsError` 清空。
    await loadRequests(true)
    if (failure) requestsError.value = failure
  }
}

/** 撤回确认框点了「确认」：这才真发那条 DELETE。 */
async function confirmCancel() {
  const target = cancelTarget.value
  cancelTarget.value = null
  if (target) await cancelLeave(target)
}

/** 「拒绝」确认框点了「确认」：这才真发。同意那条不走这儿（见 `answerTarget` 的注释）。 */
async function confirmAnswer() {
  const target = answerTarget.value
  answerTarget.value = null
  if (target) await answerSwap(target.card, target.agree)
}

/** 读卫生那一块（票 10）：今天的日常清单 + 本班的逾期钟点。
 *
 *  与排班那张卡**互不牵连**：它自己一个 try，失败只把这张卡变成一句「读不出来 + 重试」，
 *  上面那张排班卡照常显示（反过来也一样）。两个请求并行发，弱网下不用等两轮。
 */
async function loadHygiene(quiet = false) {
  // 同上：静默重读不打回 loading 态。
  if (!quiet) hygiene.value = { ...hygiene.value, state: 'loading', error: '' }
  try {
    const [me, work, attireShot] = await Promise.all([
      staffRequest('/api/hygiene/staff/me'),
      staffRequest('/api/hygiene/staff/daily-work'),
      // 仪容仪表那一条（票 12）：跟日常一起拉，但**分开存** —— 它没有钟点、不按工作区。
      staffRequest('/api/hygiene/staff/attire'),
    ])
    const employee = me.employee || {}
    const shot = attireShot.attire || {}
    attire.value = {
      state: 'ready',
      error: '',
      businessDate: shot.business_date || '',
      required: Boolean(shot.required),
      status: shot.status || 'todo',
      note: shot.note || '',
      hasStandard: Boolean(shot.has_standard),
    }
    hygiene.value = {
      state: 'ready',
      error: '',
      items: work.items || [],
      clocks: me.daily_clocks || null,
      shift: employee.shift || null,
      zoneName: employee.zone_name || '',
      // 逾期是「现在几点」跟本班钟点比出来的：进页面那一刻取一次，别在 computed 里
      // 反复取时间（那样每次渲染结果都可能不一样）。
      now: Date.now(),
    }
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    hygiene.value = { ...hygiene.value, state: 'error', error: err.message || '卫生待办读不出来' }
    attire.value = { ...attire.value, state: 'error', error: err.message || '仪容仪表读不出来' }
  }
}

onMounted(() => {
  document.title = '今天'
  load()
  // 顺手读一次自己的申请：有等着批的、或别人问我换班的，首页就能看见（读不出来不吭声）。
  loadRequests(true)
  // 卫生那一块自己拉（票 10），跟排班卡并行、失败互不影响。
  loadHygiene()
})

// 实时（票 10 收尾）：店长改了我的班、批了我的假、有人找我换班、或者我这区的卫生
// 待办变了 —— 三块各拉各的（都是轻量 GET，nudge 本身不带数据），一次全刷到。
// 换班那条尤其要紧：对方不开页面就永远不知道有人找他换。
useNudgePull({
  id: 'today-page',
  topics: ['scheduling', 'hygiene'],
  pull: () => {
    load(true)
    loadRequests(true)
    loadHygiene(true)
  },
})
</script>

<template>
  <div class="today-page hygiene-staff">
    <header class="tTop">
      <span class="tDay">今天</span>
      <span class="tDate">{{ today ? dayLabel(today.business_date) : '' }}</span>
      <span v-if="employee" class="tMe">{{ employee.name }}</span>
      <!-- 三张员工页共用的退出（票 10）：顶栏右上角同一颗按钮、同一套逻辑。 -->
      <StaffExitButton />
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

        <!-- 进页面那次申请列表没读出来：换班卡是「有才显示」的，读失败时它整块不在 ——
             不说一句，员工只会以为没人找他换班（页面上也没别的重来路径）。 -->
        <section v-if="requestsUnread" class="tA-card leave">
          <div class="tA-hd">
            <span class="tag leave">我的申请</span>
            <em>没读出来</em>
          </div>
          <p class="tA-sub">有没有人找你换班、你的申请到哪一步，这一次没读到（网络不好？）。</p>
          <div class="acts">
            <button
              class="btn"
              type="button"
              :disabled="leaveBusy || swapBusy"
              @click="loadRequests()"
            >重试</button>
          </div>
        </section>

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
                  @click="answerTarget = { card, agree: false }"
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
                @click="cancelTarget = request"
              >撤回</button>
            </li>
          </ul>
          <p class="tA-sub">
            请假批了那天写「请假」；换班要对方先同意、店长再批，批完两个人才对调。
          </p>
        </section>

        <!-- 卫生那块（原型 A 的下半张卡，票 10 接上）：今天的日常检查进度、逾期点、
             还差几项。跟上面那张排班卡各拉各的 —— 一块读不出来，另一块照常显示。 -->
        <section class="tA-card hyg">
          <div class="tA-hd">
            <span class="tag hyg">卫生</span>
            <em v-if="hygiene.state === 'ready' && hygiene.zoneName">{{ hygiene.zoneName }}</em>
          </div>

          <p v-if="hygiene.state === 'loading'" class="tA-sub">正在读今天的日常…</p>

          <template v-else-if="hygiene.state === 'error'">
            <p class="tA-sub">{{ hygiene.error }}</p>
            <div class="acts">
              <button class="btn" type="button" @click="loadHygiene()">重试</button>
            </div>
          </template>

          <template v-else>
            <p v-if="!hygieneStats.total" class="tA-sub">
              <template v-if="hygiene.shift">今天没有要交的日常检查。</template>
              <!-- 没班次就没有可交的那一份（票 10：班次由排班决定）——说清该找谁，别让人在这儿等。 -->
              <template v-else>排班还没给你班次，今天没有可交的日常检查；找店长配一下规则。</template>
            </p>
            <template v-else>
              <p class="shift">日常 {{ hygieneStats.passed }}/{{ hygieneStats.total }}</p>
              <p class="tA-sub">
                <template v-if="hygieneStats.remaining">还差 {{ hygieneStats.remaining }} 项</template>
                <template v-else>今天的日常交齐了</template><template
                  v-if="hygieneDue"
                >，本班 {{ hygieneDue }} 前交</template>。
              </p>
              <p v-if="hygieneOverdue" class="tL-err" role="status">
                已经过了本班的钟点，还有 {{ hygieneOverdue }} 项没交。
              </p>
            </template>
            <!-- 仪容仪表（票 12）：按**人**拍，今天排到班次才有这一行 —— 休假的与没
                 排到的人看不到它。跟上面那份日常分开：它没有钟点、也不挂在工作区上。 -->
            <template v-if="attire.required">
              <p class="attire-line" :class="attireTone">
                仪容仪表：<template v-if="attirePending">正在传…</template><template
                  v-else-if="attire.status === 'passed'"
                >已通过</template><template
                  v-else-if="attire.status === 'pending'"
                >已交，等店长验收</template><template
                  v-else-if="attire.status === 'rejected'"
                >被驳回，要重拍</template><template
                  v-else-if="!attire.hasStandard"
                >店长还没传标准图</template><template v-else>今天还没拍</template>
              </p>
              <p v-if="attire.note" class="tL-err" role="status">店长说：{{ attire.note }}</p>
              <p v-if="attire.error" class="tL-err" role="status">{{ attire.error }}</p>
              <!-- 已经通过的不给重拍入口（服务端也会拒 `already_accepted`）：验收过的是
                   记录，员工端就不该摆一个按了必然失败的按钮。 -->
              <div v-if="attire.hasStandard && attire.status !== 'passed'" class="acts">
                <button
                  class="btn"
                  type="button"
                  :disabled="attirePending"
                  @click="openAttireStandard"
                >
                  {{ attire.status === 'todo' ? '对着标准图拍 ›' : '重拍一张 ›' }}
                </button>
              </div>
            </template>

            <div class="acts">
              <button class="btn" type="button" @click="router.push('/workbench/me/clean')">
                {{ hygieneStats.remaining ? '去交 / 继续验收 ›' : '去卫生待办 ›' }}
              </button>
            </div>
          </template>
        </section>

        <p class="tA-foot">
          排班只说班次，不说几点上班 —— 钟点只有卫生那边才有（逾期点）。
        </p>
      </template>
    </div>

    <!-- 仪容仪表的拍照弹层（票 12）：两步 —— 先看标准图、再开相机。**不同屏**是 ADR
         0050 的硬规则：标准图与取景器并排，人会照着「上一张」摆姿势而不是看镜头。 -->
    <div
      v-if="attireSheet"
      class="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="attire-sheet-title"
      @click.self="attireSheet = ''"
    >
      <div class="modal-box">
        <div class="modal-header">
          <h3 id="attire-sheet-title">仪容仪表</h3>
          <button class="btn" type="button" @click="attireSheet = ''">关闭</button>
        </div>
        <template v-if="attireSheet === 'standard'">
          <p class="tA-sub">对着标准图看清再拍。这一屏故意不放取景框。</p>
          <HygieneStandardOverlay
            src="/api/hygiene/staff/attire/standard"
            alt="仪容仪表标准图"
          />
          <button class="btn" type="button" @click="openAttireCamera">打开相机</button>
        </template>
        <template v-else-if="attireSheet === 'camera'">
          <p class="tA-sub">拍现在的样子。相册里的旧照片不算，必须现场拍。</p>
          <HygieneLiveCamera @captured="onAttireCaptured" />
        </template>
      </div>
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
          <!-- `min` 用**服务端的营业日**（不是手机上的今天）：过去的日子服务端一定会拒
               （`past_leave`），就在这里先挡住，别让员工拨完日期、提交了才被打回来。
               上限（排班铺到哪天）不在这里设：`/me` 没下发窗口末日，服务端那句
               「排班还没铺到那么远：最多请到 X」说得比控件准。 -->
          <input
            id="leave-start"
            v-model="leaveStart"
            class="input"
            type="date"
            :min="today ? today.business_date : undefined"
          >
        </div>
        <div class="form-row">
          <label for="leave-end">到哪天（可空）</label>
          <input
            id="leave-end"
            v-model="leaveEnd"
            class="input"
            type="date"
            :min="leaveStart || (today ? today.business_date : undefined)"
          >
        </div>
        <div class="form-row">
          <label for="leave-note">{{ noteLabel }}</label>
          <input
            id="leave-note"
            v-model="leaveNote"
            class="input"
            type="text"
            :maxlength="maxNote || undefined"
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
          <input
            id="swap-day"
            v-model="swapDay"
            class="input"
            type="date"
            :min="today ? today.business_date : undefined"
          >
        </div>
        <div class="form-row">
          <label for="swap-note">{{ noteLabel }}</label>
          <input
            id="swap-note"
            v-model="swapNote"
            class="input"
            type="text"
            :maxlength="maxNote || undefined"
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

    <!-- 撤回 / 拒绝先过确认框（不可逆，手机上一误触没有回头路）。同意不弹：
         后面还有店长那道闸，跟店长端「批准不弹、驳回弹」同一个口径。 -->
    <ConfirmDialog
      v-if="cancelTarget"
      title="撤回这条申请"
      message="撤回后这条申请就结束了，要重新提一次。排班一个字不改。"
      confirm-label="撤回"
      danger
      @confirm="confirmCancel"
      @cancel="cancelTarget = null"
    />
    <ConfirmDialog
      v-if="answerTarget"
      title="拒绝这次换班"
      message="拒绝之后这件事就结束了，对方会看到你拒绝了；你的班照旧。"
      confirm-label="拒绝"
      danger
      @confirm="confirmAnswer"
      @cancel="answerTarget = null"
    />
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

/* 仪容仪表那一行（票 12 加的功能）自己的字号：它是一句话 + 一个状态，不是数值。
   原来借用了下面的 `.shift`（52px 的英雄数字），一句话会占两三行、压过「日常 n/m」。
   中等字号 + 按状态着色，仍看得出「这条要你动手」。 */
.attire-line {
  margin: 0;
  font-family: var(--font-song);
  font-size: 20px;
  line-height: 1.3;
  color: var(--hy-jade);
  padding: 2px 0;
}

.attire-line.pending { color: var(--hy-muted); }
.attire-line.rejected { color: var(--hy-coral); }
.attire-line.nostandard { color: var(--hy-amber); }

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
  /* 员工手机上点的东西：这一排按钮（同意 / 拒绝 / 撤回）都是不可逆动作，手指先得点得中。
     仓库里员工端其它按钮是 46px；这里给 40px 是因为一行里要并排放两个 —— 32px 太小了，
     误触的代价是一张换班申请被拒或者一条假被撤回。 */
  min-height: 40px;
  padding: 0 14px;
  font-size: 12.5px;
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
