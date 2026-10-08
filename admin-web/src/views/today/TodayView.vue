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
 * 「我的成绩」那张卡（2026-10-05 用户裁定）另读一条 `GET /api/hygiene/staff/me/stats`，
 * 同样只认员工自己的 cookie（路径上没有 `employee_id`，读谁由服务端按会话定）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import HygieneLiveCamera from '../../components/hygiene/HygieneLiveCamera.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import { useNudgePull } from '../../composables/useNudgePull'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
import { ADMIN_CAP_STAFF_DEFS, hasCap } from '../../utils/adminCaps'
import { hygieneShiftLabel } from '../../utils/hygieneCopy'
import { staffRequest } from '../../utils/hygieneStaff'
import { buildWorkQueue, dailyProgress, shiftClock } from '../../utils/hygieneWorkFlow'
import { canCancel, incomingLine, requestLine } from '../../utils/leaveRequest'
import { loginRedirectTarget } from '../../utils/loginNext'
import { birthdayText } from '../../utils/birthdayReminder'
import { monthText } from '../../utils/seniorityReminder'
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

// 「我的」那一块（从卫生页的「我」搬来，界面与逻辑在文件后半段）的**数据**：都跟着
// `loadHygiene` 那条 `/api/hygiene/staff/me` 一起下来 —— `staffMe` 就是那份 employee
// （姓名 / 手机号 / 职位 / 当天的区与班次，外加「你能做的事」那张卡要的 `admin_caps`），
// `deepClock` 是专项的钟点。两条都是**同一个响应**，
// 不再为「我的」多发一条请求。声明摆在这里，是因为给它们赋值的那一步在下面。
const staffMe = ref(null)
const deepClock = ref(null)

// 「你能做的事」能力卡（票 01 / ADR 0093）的数据：十项开关里**员工端真有执行点**的那三项
// （`ADMIN_CAP_STAFF_DEFS` 与后端 `STAFF_SIDE_CAPABILITIES` 同义），逐项标出开了没有。
// 判据一律看 `admin_caps` —— `permission` 只是显示用的标签（真机走查 S10：标签写着
// 「管理员」而一项开关都没给是合法状态），拿标签判会放行没给的那件事。
// 未接线的那七项**不进这张卡**：它们对应的是超级管理员在电脑端的活，员工端没有入口，
// 画出来就是"勾了不生效"那类缺陷的另一面。
const staffCaps = computed(() => ADMIN_CAP_STAFF_DEFS.map((item) => ({
  ...item,
  granted: hasCap(staffMe.value && staffMe.value.admin_caps, item.key),
})))
const staffCapCount = computed(() => staffCaps.value.filter((item) => item.granted).length)

// 健康证到期（一行提示，只在临期 / 过期时渲染）：文案里**不带日期** —— 日期在下面
// 「我的」那两行里，同一件事不说两遍。数据来自 `/api/hygiene/staff/me` 的
// `health_cert_state`（服务端按北京时自然日算，前端不做日期算术）。
const healthCertAlert = computed(() => {
  const state = staffMe.value && staffMe.value.health_cert_state
  if (state === 'soon') return '健康证快到期，请尽快办理'
  if (state === 'expired') return '健康证已过期，请尽快办理'
  return ''
})
const healthCertTone = computed(() => (
  (staffMe.value && staffMe.value.health_cert_state) === 'soon' ? 'is-soon' : ''
))

// 工龄奖（票 05，`docs/adr/0101`）：档位是档案里落库的那一栏（超管在花名册 / 人事提醒里
// 维护），下次调整月是服务端按入职日期派生的 —— 前端不做日期算术，月份只做「2027-09 →
// 2027 年 9 月」这一层翻译（`utils/seniorityReminder.js`，与提醒页同一份）。没调过时
// 说「未调整」而不是 0 元：这个人的工龄奖是空着，不是零。
const seniorityText = computed(() => {
  const value = staffMe.value && staffMe.value.seniority_bonus
  return value === null || value === undefined ? '未调整' : `${value} 元/月`
})
const seniorityNextText = computed(() => {
  const me = staffMe.value
  if (!me || !me.hire_date) return ''
  const month = monthText(me.seniority_next_adjust_month)
  return month ? `${month}调整` : '已封顶'
})

// 「我的成绩」（2026-10-05 用户裁定）：近 7 天的一次通过率 + 被驳回的原因分布。
//
// **为什么放这一页**：员工端原来只有「今天要做什么」——惩罚（驳回、红黑榜）看得见，
// 正反馈一点没有，人会躲着这个系统用。这一页是员工**登录后的落点**（`/login` 的员工栏
// 就落在这儿，见 `utils/loginNext.js`），休假的、当天没排到班的日子照样打得开；卫生页
// `/workbench/me/clean` 只有"今天有活"的时候才会被点开 —— 恰恰是那些被驳回过、最该看
// 一眼自己成绩的人，那天可能压根没有活干，也就永远看不到这张卡。成绩记的是**验收之后**
// 的结果，不是交照片那一刻，也不需要贴着取景框才有意义。
// 卡的位置在卫生卡与「我的」之间：这一页的读序是"今天上不上班 → 今天的活 → 我干得
// 怎么样 → 账号设置"，成绩属于第三段，而「我的」那张是设置（不是活）。
const SCORE_DAYS = 7
const score = ref({
  state: 'loading', // loading | ready | error
  error: '',
  // `passRate === null` = 这段时间**一次都没交过活**（服务端就用 null 表示"没有分母"）。
  // 它与 `0` 是两件事：0% 是"交了全被驳"，null 是"还没开始"。界面上必须分开（见模板里
  // 那个分支）—— 给新人看 0%，他只会以为自己已经被扣分了，那正是这张卡要治的毛病。
  passRate: null,
  firstPass: 0,
  rejected: 0,
  reasons: [],
  days: SCORE_DAYS,
})

// 顶上那张排班卡（「重新选择区域和班次」那一行要把人送回它）：
const schedCard = ref(null)

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
// 落点算在 `loginRedirectTarget` 一处（票 12 收的 O2）：页面上并发的几个请求会各拿一个
// 401 各自调到这里，**已经在登录页上时它返回 null** —— 不返回的话第二个 401 会把
// 「已经是登录页的当前地址」再包一层，`?next=` 里的目标当场作废。
function leaveForStaffLogin() {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
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
    // 同一份响应里那个 employee 也喂「我的」那一块与「你能做的事」那张卡（姓名 / 手机号 /
    // 职位 / 班次 / `admin_caps`，见下面 `staffMe`）：它是**同一个** `/api/hygiene/staff/me`，
    // 不额外再发一条请求。
    staffMe.value = employee
    deepClock.value = me.deep_clock || null
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

/** 大数字那位：服务端给的是 0–1 的小数。 */
const scorePercent = computed(() => {
  if (score.value.passRate === null) return null
  const percent = Math.round(score.value.passRate * 100)
  // 0.996 这种不能显示成 100%：这张卡上就写着「被驳回 N 次」，两个数并排自相矛盾 ——
  // 少一个百分点的精度，比让员工发现页面在骗他要轻得多。
  return percent === 100 && score.value.passRate < 1 ? 99 : percent
})

/** 读自己的成绩（近 7 天）。跟卫生那块一样**自己一个 try**：读不出来只把这卡变成一句
 *  「读不出来 + 重试」，上面那几张卡照常显示。 */
async function loadScore(quiet = false) {
  if (!quiet) score.value = { ...score.value, state: 'loading', error: '' }
  try {
    const data = await staffRequest(`/api/hygiene/staff/me/stats?days=${SCORE_DAYS}`)
    score.value = {
      state: 'ready',
      error: '',
      passRate: data.pass_rate === null || data.pass_rate === undefined
        ? null
        : Number(data.pass_rate),
      firstPass: Number(data.first_pass) || 0,
      rejected: Number(data.rejected) || 0,
      reasons: Array.isArray(data.reasons) ? data.reasons : [],
      // 窗口用响应里那个 `days`：服务端有 84 天保留期这一道夹子，拿它的数显示才不会跟
      // 真实窗口对不上（文案里写死「7 天」，服务端一改口径这张卡就开始撒谎）。
      days: Number(data.days) || SCORE_DAYS,
    }
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    score.value = { ...score.value, state: 'error', error: err.message || '成绩读不出来' }
  }
}

// 「我的」那一块（2026-10-05 用户裁定）——**从卫生页整块搬来**。
//
// 原来它在 `/workbench/me/clean` 是第五格 tab「我」，与待办 / 专项 / 整改 / 榜四条平级：
// 一条 tab 条上混了三种性质（任务 / 信息 / 设置）。账号设置属于「我的」这一页
// （底栏那一格指的就是 `/workbench/me/today`），所以连人带逻辑搬到这一页尾部：
// 三行入口（修改个人信息 / 修改密码 / 重新选择区域和班次）+ 一条自己的资料。
// 逻辑是照搬的（手机号正则、改号前那次确认、两条 PATCH、成功与失败的提示语），
// 只换了容器写法：这一页的表单走 `.form-row` / `.input` / `.modal-*`（与请假、换班
// 同一个底子），不再借用卫生页那套 `.staff-field` / `.staff-input`（那套样式的作用域
// 是 `.hygiene-work`，在「今天」页上落不下来）。
const profileOpen = ref(false)
const profileName = ref('')
const profilePhone = ref('')
const profileSaving = ref(false)
const profileError = ref('')
const profileFlash = ref('')
const profilePhoneConfirmOpen = ref(false)
const passwordOpen = ref(false)
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordSaving = ref(false)
const passwordError = ref('')
const passwordFlash = ref('')

// 专项截止（原来也在「我」那张资料里）：钟点随 `/api/hygiene/staff/me` 下来，跟日常那个
// `hygieneDue` 一个来源，所以这一页不再另发请求。
const deepDue = computed(() => (deepClock.value && deepClock.value.hhmm) || '')

function openProfile() {
  passwordOpen.value = false
  profileError.value = ''
  profileFlash.value = ''
  profileName.value = (staffMe.value && staffMe.value.name) || ''
  profilePhone.value = (staffMe.value && staffMe.value.phone) || ''
  profileOpen.value = true
}

function closeProfile() {
  profileOpen.value = false
  profileError.value = ''
}

const PHONE_PATTERN = /^1[3-9]\d{9}$/

/** 手机号是登录账号，改错一位 = 下次登不进来。改号必须先确认。 */
function phoneChanged() {
  const current = String((staffMe.value && staffMe.value.phone) || '')
  return profilePhone.value.trim() !== current
}

async function saveProfile() {
  if (profileSaving.value || !staffMe.value) return
  const nextPhone = profilePhone.value.trim()
  if (!PHONE_PATTERN.test(nextPhone)) {
    profileError.value = '手机号格式不对，应该是 11 位、以 1 开头的号码。'
    return
  }
  // 姓名随便改，手机号不行：它是登录账号，且 30 天内只能靠管理员救回来。
  if (phoneChanged() && !profilePhoneConfirmOpen.value) {
    profilePhoneConfirmOpen.value = true
    return
  }
  profilePhoneConfirmOpen.value = false
  profileSaving.value = true
  profileError.value = ''
  profileFlash.value = ''
  try {
    const data = await staffRequest('/api/hygiene/staff/me', {
      method: 'PATCH',
      body: {
        name: profileName.value.trim(),
        phone: nextPhone,
      },
    })
    staffMe.value = { ...staffMe.value, ...(data.employee || {}) }
    // 顶栏那颗名字来自排班那份 employee（`/api/scheduling/me`）：改完顺手抹平，
    // 不然这一页顶上还挂着旧名字，得刷新才变。
    if (employee.value && data.employee && data.employee.name) {
      employee.value = { ...employee.value, name: data.employee.name }
    }
    profileOpen.value = false
    profileFlash.value = '个人信息已保存，手机号下次登录生效。'
  } catch (err) {
    // 401 回员工登录：与这一页其它几条请求同一个走法（原卫生页那两处没写这一段，
    // 搬过来时补齐 —— 会话过期时不该只把话咽成一句「保存失败」）。
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    profileError.value = err.message || '保存个人信息失败'
  } finally {
    profileSaving.value = false
  }
}

function openPassword() {
  profileOpen.value = false
  passwordError.value = ''
  passwordFlash.value = ''
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
  passwordOpen.value = true
}

function closePassword() {
  passwordOpen.value = false
  passwordError.value = ''
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
}

async function savePassword() {
  if (passwordSaving.value) return
  passwordError.value = ''
  passwordFlash.value = ''
  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = '两次输入的新密码不一致'
    return
  }
  passwordSaving.value = true
  try {
    await staffRequest('/api/hygiene/staff/password', {
      method: 'PATCH',
      body: {
        current_password: currentPassword.value,
        new_password: newPassword.value,
        confirm_password: confirmPassword.value,
      },
    })
    passwordOpen.value = false
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    passwordFlash.value = '密码已修改，其他设备上的登录已失效。'
  } catch (err) {
    if (err.status === 401) {
      leaveForStaffLogin()
      return
    }
    passwordError.value = err.message || '修改密码失败'
  } finally {
    passwordSaving.value = false
  }
}

/** 第三行「重新选择区域和班次」：票 10 起员工**不能**自选班次和工作区 —— 今天在哪由
 *  排班决定。卫生页那一版点了是滚回待办那一屏看说明；在这一页，说明和班就在顶上那张
 *  排班卡里，所以这里把话写进它的提示位，再把页面送回顶部（那就是「去看我的班」）。
 *  不摆一个改不动的选择器：那正是票 10 撤掉的东西。
 *  用模板 ref 而不是 `document.querySelector`：那张卡只在读到了班的时候才在（loading /
 *  出错时不渲染），拿 ref 少一次"查不到就当没点"的静默分支。 */
function openShiftNotice() {
  note.value = '今天上哪个班、在哪个区由排班决定；要改哪一天，找店长在排班页改。'
  const card = schedCard.value
  if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(() => {
  // 名字与页面清单（`router/pageRoutes.json` 里 `/workbench/me/today` 那一行）同一个：
  // 它原来是「今天」，与工作台首页（`/workbench`，也报「今天」）**撞名** —— 真机走查里
  // 员工登录落到这一页、底栏第一格「今天」却指向另一页，两页浏览器标签一模一样。
  document.title = workbenchDocumentTitle('我的今天')
  load()
  // 顺手读一次自己的申请：有等着批的、或别人问我换班的，首页就能看见（读不出来不吭声）。
  loadRequests(true)
  // 卫生那一块自己拉（票 10），跟排班卡并行、失败互不影响。
  loadHygiene()
  // 「我的成绩」也自己拉：它读的是另一条接口（`/staff/me/stats`），弱网下不用排在
  // 卫生那三跳后面。
  loadScore()
})

// 实时（票 10 收尾）：店长改了我的班、批了我的假、有人找我换班、或者我这区的卫生
// 待办变了 —— 三块各拉各的（都是轻量 GET，nudge 本身不带数据），一次全刷到。
// 换班那条尤其要紧：对方不开页面就永远不知道有人找他换。
// 成绩那一块也在这儿重读：验收（通过 / 驳回）是店长在别处做的动作，nudge 是这张卡
// 唯一能自己变新的机会 —— 不挂在这条上，员工得等下次开页面才看得到新数字。
useNudgePull({
  id: 'today-page',
  topics: ['scheduling', 'hygiene'],
  pull: () => {
    load(true)
    loadRequests(true)
    loadHygiene(true)
    loadScore(true)
  },
})
</script>

<template>
  <div class="today-page hygiene-staff">
    <header class="tTop">
      <span class="tDay">今天</span>
      <span class="tDate">{{ today ? dayLabel(today.business_date) : '' }}</span>
      <span v-if="employee" class="tMe">{{ employee.name }}</span>
      <!-- 退出**不在这里**（D5）：工作台外壳顶栏已经有一颗（`WorkbenchLayout` 的
           `WorkbenchExitButton`，员工那一档走同一条 `useStaffLogout`），这一页原来又挂了
           一颗同名的 `.staff-exit` —— 手机上两者相距约 300px、功能完全重复。 -->
    </header>

    <div class="tA-body">
      <!-- 健康证提示（一条一行，不可关闭）：整页最上面 —— 它是"你得去办证了"，
           比排班与今天的活都更早要处理。日期不写在这句里（下面「我的」那两行有）。 -->
      <p
        v-if="healthCertAlert"
        class="hy-staff-alert health-cert-alert"
        :class="healthCertTone"
        role="status"
      >{{ healthCertAlert }}</p>

      <p v-if="state === 'loading'" class="tA-sub">正在读你的班…</p>

      <template v-else-if="state === 'error'">
        <p class="tA-sub">{{ errorText }}</p>
        <button class="btn btn-block" type="button" @click="load">重试</button>
      </template>

      <template v-else>
        <!-- 「你能做的事」（票 01 / ADR 0093）：被放权的员工在这一页看清自己能做哪几件事。
             为什么要占页面最上面：这一页是他登录后的落点，而"我能做什么"是读这一页之前
             就得知道的事 —— 原来这信息藏在页尾账号信息那一行里，等于没写。
             完全没有权限的员工看不到这一块：判据是 `staffCapCount`（那三项里一项都没开），
             界面就不给他一个空洞的头衔 —— 挂在 `permission` 标签上就会给（标签写着
             「管理员」而一项开关都没给是合法状态，见 ADR 0093）。 -->
        <section v-if="staffCapCount" class="tA-card caps">
          <div class="tA-hd">
            <span class="tag caps">你能做的事</span>
            <!-- 档位那颗胶囊（`现场复核 · n 项`，与卫生页页头那颗同一个措辞）：原来在账号
                 信息里的「卫生权限」那一行，票 01 起搬到这里 —— 同一件事只说一遍。 -->
            <em>现场复核 · {{ staffCapCount }} 项</em>
          </div>
          <ul class="cap-list">
            <!-- 三项**都列**：没开的那一项写「店长还没开给你」，员工才知道该找店长开什么，
                 而不是以为系统就是不给（只列开了的那几项，缺的那一项就永远看不见）。 -->
            <li
              v-for="item in staffCaps"
              :key="item.key"
              class="cap-row"
              :class="{ off: !item.granted }"
            >
              <span class="cap-mark" aria-hidden="true">{{ item.granted ? '✓' : '—' }}</span>
              <span class="cap-name">{{ item.label }}</span>
              <span class="cap-note">{{ item.granted ? item.note : '店长还没开给你' }}</span>
            </li>
          </ul>
          <!-- 脚注 + 入口：这三项的执行点全在手机端「卫生」页（`/workbench/me/clean`），
               员工在电脑上找是找不到的 —— 所以这里直接给路，不让他自己猜。
               「卫生」那一页自己也有一条从「今天」页过去的路（那张卫生卡上），两处不冲突：
               这一条属于"知道了能做什么，接着去做"。 -->
          <p class="tA-sub">这三项都在手机端「卫生」页里做。</p>
          <div class="acts">
            <button class="btn" type="button" @click="router.push('/workbench/me/clean')">
              去「卫生」页 ›
            </button>
          </div>
        </section>

        <section ref="schedCard" class="tA-card sched">
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
                  {{ attire.status === 'todo' ? '仪容仪表 ›' : '重拍一张 ›' }}
                </button>
              </div>
            </template>

            <!-- 入口名（2026-10-08 用户定名）：跟上面那颗「仪容仪表 ›」对称 —— 一个指人、
                 一个指区，都不再随「还差几项」变。原来的「去交 / 继续验收 ›」把两个动作
                 并列在一颗按钮上，而「验收」对普通员工不是他们的活。 -->
            <div class="acts">
              <button class="btn" type="button" @click="router.push('/workbench/me/clean')">
                工作卫生 ›
              </button>
            </div>
          </template>
        </section>

        <!-- 「我的成绩」（2026-10-05 用户裁定）：近 7 天的一次通过率 + 被驳回的原因。
             为什么放在这一页、为什么 `null` 不能显示成 0%，见脚本里 `score` 与
             `scorePercent` 那两段。 -->
        <section class="tA-card score">
          <div class="tA-hd">
            <span class="tag score">成绩</span>
            <em>近 {{ score.days }} 天</em>
          </div>

          <p v-if="score.state === 'loading'" class="tA-sub">正在读你的成绩…</p>

          <template v-else-if="score.state === 'error'">
            <p class="tA-sub">{{ score.error }}</p>
            <div class="acts">
              <button class="btn" type="button" @click="loadScore()">重试</button>
            </div>
          </template>

          <!-- 还没开始（`pass_rate === null`）：这一格**不给任何数字**，包括 0%。
               0% 是"交了但都被驳"，"还没交过"是另一件事 —— 给新人看 0%，他会当成
               已经被扣了一分，而这张卡存在的理由正好相反：让他愿意交第一项。 -->
          <template v-else-if="scorePercent === null">
            <p class="score-empty">近 {{ score.days }} 天还没交过活，交一项就有记录。</p>
            <p class="tA-sub">一次通过率要交了第一项才算得出来，现在不算你落后。</p>
          </template>

          <template v-else>
            <div class="score-body">
              <p class="score-rate">{{ scorePercent }}<span>%</span></p>
              <dl class="score-split">
                <div>
                  <dt>一次通过</dt>
                  <dd>{{ score.firstPass }} 项</dd>
                </div>
                <div>
                  <dt>被驳回</dt>
                  <dd>{{ score.rejected }} 次</dd>
                </div>
              </dl>
            </div>
            <p class="tA-sub">
              一次通过率 = 一次通过 ÷（一次通过 + 被驳回）；跟店长看的红黑榜是同一份记录。
            </p>
          </template>

          <!-- 驳回原因比数字更有用：员工要知道的是"我老在哪件事上栽"。一行一条、带次数
               （服务端已按次数排好、最多 5 条）。一条原因都没有时整块不出现。 -->
          <div v-if="score.reasons.length" class="score-reasons">
            <p class="score-reasons-hd">被驳回的地方</p>
            <ul>
              <li v-for="item in score.reasons" :key="item.reason">
                <span>{{ item.reason }}</span>
                <em>{{ item.count }} 次</em>
              </li>
            </ul>
          </div>
        </section>

        <!-- 「我的」那一块（2026-10-05 用户裁定，**从卫生页的「我」整格搬来**）：
             三行入口 + 一张自己的资料。放在页面尾部 —— 这一页开头是「今天上不上班」、
             中间是今天的活，账号设置是收尾的事。
             三行入口各自开一张弹层（与上面请假 / 换班同一个底子），不在这里再长出一套
             卫生页的页内表单；「重新选择区域和班次」第三行不弹表单（票 10 起员工不能自选），
             它把说明写进顶部那张排班卡并把人送回去 —— 见 `openShiftNotice`。 -->
        <section class="tA-card me">
          <div class="tA-hd">
            <span class="tag me">我的</span>
            <em v-if="staffMe">{{ staffMe.name || staffMe.phone }}</em>
          </div>
          <p v-if="profileFlash" class="me-flash" role="status">{{ profileFlash }}</p>
          <p v-if="passwordFlash" class="me-flash" role="status">{{ passwordFlash }}</p>
          <dl v-if="staffMe" class="me-meta">
            <div>
              <dt>姓名</dt>
              <dd>{{ staffMe.name || '未设置' }}</dd>
            </div>
            <div>
              <dt>手机号</dt>
              <dd>{{ staffMe.phone }}</dd>
            </div>
            <div>
              <dt>当天区域</dt>
              <dd>{{ staffMe.zone_name || '未选' }}</dd>
            </div>
            <div>
              <dt>当天班次</dt>
              <dd>{{ hygieneShiftLabel(staffMe.shift) }}</dd>
            </div>
            <div v-if="hygieneDue">
              <dt>本班日常截止</dt>
              <dd>{{ hygieneDue }} 前交</dd>
            </div>
            <div v-if="deepDue">
              <dt>专项截止</dt>
              <dd>{{ deepDue }} 前做完</dd>
            </div>
            <div>
              <dt>职位</dt>
              <dd>{{ staffMe.job_title || '未设置' }}</dd>
            </div>
            <!-- 档案两行（2026-10 花名册改版）：入职日期与健康证到期日由超管在花名册里补录，
                 员工自己只读。底薪与身份证号**不下发也不显示**（敏感字段边界）。 -->
            <div>
              <dt>入职日期</dt>
              <dd>{{ staffMe.hire_date || '未设置' }}</dd>
            </div>
            <div>
              <dt>健康证到期日</dt>
              <dd>{{ staffMe.health_cert_expires_on || '未设置' }}</dd>
            </div>
            <!-- 工龄奖（票 05）：档位与下次调整月都是**自己那份**信息（入职日期本来就
                 在这儿显示），所以这一行不碰敏感字段边界。入职日期没补时不渲染它 ——
                 算不出来就不摆一个「未调整」在那儿让人误会。 -->
            <div v-if="staffMe.hire_date">
              <dt>工龄奖</dt>
              <dd>
                {{ seniorityText }}
                <template v-if="seniorityNextText"> · {{ seniorityNextText }}</template>
              </dd>
            </div>
            <!-- 生日（票 06）：**从身份证号派生出来单独下发的那一列**，身份证号本身
                 一条都不下发（`docs/adr/0098` / `0102`）。读不出身份证就不渲染这一行 ——
                 没有生日不是「1 月 1 日」，不该摆一个值在那儿让人误会。 -->
            <div v-if="staffMe.birthday">
              <dt>生日</dt>
              <dd>{{ birthdayText(staffMe.birthday) }}<span class="tL-hint">（按身份证）</span></dd>
            </div>
          </dl>
          <ul class="tL-list">
            <li>
              <span class="tL-line">修改个人信息</span>
              <!-- 资料（`staffMe`）没读出来时这两行点不动：表单要拿姓名与手机号做初值，
                   点开了也只会是空的（原来「我」那一格整块挂在 `v-if="employee"` 上，
                   同一个保证）。第三行不依赖它 —— 它只是把人送回顶上那张排班卡。 -->
              <button class="btn tL-cancel" type="button" :disabled="!staffMe" @click="openProfile">修改</button>
            </li>
            <li>
              <span class="tL-line">修改密码</span>
              <button class="btn tL-cancel" type="button" :disabled="!staffMe" @click="openPassword">修改</button>
            </li>
            <li>
              <span class="tL-line">重新选择区域和班次</span>
              <!-- 按钮上不写「修改」：票 10 起员工改不了它，点一下是去看为什么 + 该找谁。 -->
              <button class="btn tL-cancel" type="button" @click="openShiftNotice">怎么改</button>
            </li>
          </ul>
          <p class="tA-sub">专项全店可用；整改按你的工作区显示。所有现场照片都需实拍。</p>
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

    <!-- 修改个人信息（从卫生页的「我」搬来）：姓名随便改，手机号是登录账号 ——
         改号那一次要先过确认框（这一块最下面那个）。 -->
    <div
      v-if="profileOpen"
      class="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="profile-sheet-title"
      @click.self="closeProfile"
    >
      <div class="modal-box">
        <div class="modal-header">
          <h3 id="profile-sheet-title">修改个人信息</h3>
          <button class="btn" type="button" @click="closeProfile">关闭</button>
        </div>
        <div class="form-row">
          <label for="profile-name">姓名</label>
          <input
            id="profile-name"
            v-model="profileName"
            class="input"
            type="text"
            maxlength="40"
            autocomplete="name"
          >
        </div>
        <div class="form-row">
          <label for="profile-phone">手机号</label>
          <input
            id="profile-phone"
            v-model="profilePhone"
            class="input"
            type="tel"
            inputmode="numeric"
            maxlength="11"
            pattern="1[3-9]\d{9}"
            autocomplete="username"
          >
        </div>
        <p class="tA-sub">手机号也是登录账号；保存后请用新手机号登录。改号前会再确认一次。</p>
        <p v-if="profileError" class="tL-err" role="alert">{{ profileError }}</p>
        <div class="modal-footer">
          <button class="btn" type="button" :disabled="profileSaving" @click="closeProfile">取消</button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="profileSaving"
            @click="saveProfile"
          >{{ profileSaving ? '正在保存…' : '保存' }}</button>
        </div>
      </div>
    </div>

    <!-- 修改密码（同上，从卫生页的「我」搬来）：校验在客户端先做一遍（两次输入一致），
         真正的规则仍在服务端。 -->
    <div
      v-if="passwordOpen"
      class="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="password-sheet-title"
      @click.self="closePassword"
    >
      <div class="modal-box">
        <div class="modal-header">
          <h3 id="password-sheet-title">修改密码</h3>
          <button class="btn" type="button" @click="closePassword">关闭</button>
        </div>
        <div class="form-row">
          <label for="password-current">当前密码</label>
          <input
            id="password-current"
            v-model="currentPassword"
            class="input"
            type="password"
            autocomplete="current-password"
          >
        </div>
        <div class="form-row">
          <label for="password-new">新密码</label>
          <input
            id="password-new"
            v-model="newPassword"
            class="input"
            type="password"
            minlength="8"
            autocomplete="new-password"
          >
        </div>
        <div class="form-row">
          <label for="password-confirm">确认新密码</label>
          <input
            id="password-confirm"
            v-model="confirmPassword"
            class="input"
            type="password"
            minlength="8"
            autocomplete="new-password"
          >
        </div>
        <p class="tA-sub">修改后保留当前设备登录，其他设备上的登录会失效。</p>
        <p v-if="passwordError" class="tL-err" role="alert">{{ passwordError }}</p>
        <div class="modal-footer">
          <button class="btn" type="button" :disabled="passwordSaving" @click="closePassword">取消</button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="passwordSaving"
            @click="savePassword"
          >{{ passwordSaving ? '正在保存…' : '修改密码' }}</button>
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

    <!-- 改手机号要先确认（跟卫生页那颗一样）：手机号是登录账号，打错一位下次就登不进来。
         文案里的号码取的是输入框里那个待保存的值。 -->
    <ConfirmDialog
      v-if="profilePhoneConfirmOpen"
      title="确认改手机号"
      :message="`手机号是登录账号。改成 ${profilePhone.trim()} 之后，下次登录要用新号；打错一位就得找管理员改回来。`"
      confirm-label="确认改号"
      danger
      @confirm="saveProfile"
      @cancel="profilePhoneConfirmOpen = false"
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

/* 「我的」那一块（从卫生页的「我」搬来）：中性色 —— 设置不是活，不抢上面两张卡的色。
   资料那一段是表格式的一行一条（原来在卫生页叫 `.hy-meta`，那份样式挂在
   `.hygiene-work` 作用域下，搬到这一页落不下来，按同一套写法在这里写一份）。 */
.tA-card.me {
  border-color: var(--hy-line-strong);
  background: var(--hy-surface-2);
}

/* 「你能做的事」（票 01）：这一页上唯一一块讲**权限**的卡，位置最靠上，色取玉色那一档
   （跟"已通过"同一个色系）—— 它说的是"你能做什么"，不是"你还欠什么"。 */
.tA-card.caps {
  border-color: var(--hy-mint-line);
  background: linear-gradient(168deg, rgba(63, 224, 176, .08), transparent 62%),
    var(--hy-surface-2);
}

.tag.caps {
  color: var(--hy-jade);
  background: var(--hy-mint-soft);
  border-color: var(--hy-mint-line);
}

/* 一行一项：勾/破折号 + 名字 + 一句说明。名字那一列定宽（三个名字最长 4 字），三行
   说明才对得齐；列宽按 390 逻辑宽（原型与真机那一档）算过：卡内容 328px，最长的那句
   说明 19 字 × 12px = 228px，落在 240px 的说明列里正好一行 —— 差一点点就会甩一个字
   到第二行，很难看。 */
.cap-list {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.cap-row {
  display: grid;
  grid-template-columns: 16px 58px 1fr;
  align-items: baseline;
  gap: 7px;
}

.cap-mark {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--hy-jade);
}

.cap-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--hy-ink);
}

.cap-note {
  font-size: 12px;
  line-height: 1.5;
  color: var(--hy-muted);
}

/* 脚注紧贴上一行会被读成"第四项"，跟上面那三行拉开一点（`.tA-sub` 本身 margin 是 0）。 */
.tA-card.caps .tA-sub {
  margin-top: 10px;
}

/* 没开的那一项：整行退到背景里（破折号 + 灰字），与开了的那几行一眼分得开 ——
   「还没开给你」是**说明**，不是可用的能力。 */
.cap-row.off .cap-mark {
  color: var(--hy-faint);
}

.cap-row.off .cap-name {
  font-weight: 500;
  color: var(--hy-muted);
}

.cap-row.off .cap-note {
  color: var(--hy-faint);
}

/* 「我的成绩」：玉色那一档（跟"已通过"同一个色系）—— 这一页上其它几张卡都在说
   "还有活没干"，只有它只讲"你干得怎么样"，所以用这套里的"好"色，不摆红字。 */
.tA-card.score {
  border-color: var(--hy-mint-line);
  background: linear-gradient(168deg, rgba(63, 224, 176, .06), transparent 62%),
    var(--hy-surface-2);
}

.tag.score {
  color: var(--hy-jade);
  background: var(--hy-mint-soft);
  border-color: var(--hy-mint-line);
}

.score-body {
  display: flex;
  align-items: flex-end;
  gap: 14px;
  padding: 4px 0 2px;
}

/* 主数字用「英雄数字」那一档字号（`.shift` 是 52px）：这张卡的主角就是它。
   比排班卡那个班次小一档，不抢"今天上不上班"的第一眼。 */
.score-rate {
  margin: 0;
  font-family: var(--font-song);
  font-size: 44px;
  line-height: 1;
  letter-spacing: .02em;
  color: var(--hy-jade);
}

.score-rate span {
  margin-left: 2px;
  font-size: 18px;
  color: var(--hy-muted);
}

.score-split {
  display: grid;
  gap: 3px;
  margin: 0 0 5px;
}

.score-split > div {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.score-split dt {
  font-size: 11px;
  color: var(--hy-muted);
}

.score-split dd {
  margin: 0;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--hy-ink);
}

/* 空态那一句按正文字号排，不套上面那个 44px 的数字位（那一格本来就没有数字）。 */
.score-empty {
  margin: 10px 0 5px;
  font-family: var(--font-song);
  font-size: 19px;
  line-height: 1.5;
  color: var(--hy-jade);
}

.score-reasons {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--hy-line);
}

.score-reasons-hd {
  margin: 0 0 7px;
  font-size: 11px;
  letter-spacing: .1em;
  color: var(--hy-muted);
}

.score-reasons ul {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.score-reasons li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 12px;
  color: var(--hy-ink);
}

/* 原因那句话可能很长（服务端最多 200 字）：让它自己换行，别把右边那次数挤出屏幕。 */
.score-reasons li span {
  min-width: 0;
  overflow-wrap: anywhere;
}

/* 次数用琥珀色：是"注意这里"，不是"你被罚了"——红字留给真正要动手的报错。 */
.score-reasons li em {
  flex: none;
  font-style: normal;
  color: var(--hy-amber);
}

.tag.me {
  color: var(--hy-muted);
  background: rgba(133, 205, 198, .1);
  border-color: var(--hy-line-strong);
}

/* 保存成功那句：跟表单里的报错（`.tL-err`）分开，一眼看得出这次是成了。 */
.me-flash {
  margin: 9px 0 0;
  padding: 8px 10px;
  border-radius: var(--hy-radius-sm);
  background: var(--hy-mint-soft);
  border: 1px solid var(--hy-mint-line);
  color: var(--hy-jade);
  font-size: 11.5px;
  font-weight: 600;
}

.me-meta {
  display: grid;
  gap: 0;
  margin: 10px 0 4px;
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-md);
  background: rgba(17, 37, 41, .66);
  overflow: hidden;
}

.me-meta > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--hy-line);
}

.me-meta > div:last-child {
  border-bottom: 0;
}

.me-meta dt {
  color: var(--hy-muted);
  font-size: 11.5px;
}

.me-meta dd {
  margin: 0;
  font-weight: 600;
  font-size: 12.5px;
  text-align: right;
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

/* 「（按身份证）」这种来源说明：跟着值走、别抢值的注意力。 */
.tL-hint {
  margin-left: 4px;
  font-size: 11px;
  color: var(--hy-faint);
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

/* 健康证提示：共享表里的 `.hygiene-staff .hy-staff-alert` 是朱砂（过期那一档），
   临期这一档改琥珀 —— 父级选择器抬特异度，稳过共享表那条。 */
.health-cert-alert {
  margin: 0;
}
.health-cert-alert.is-soon {
  background: var(--hy-amber-soft);
  border-color: var(--hy-amber-line);
  color: var(--hy-amber);
}
</style>
