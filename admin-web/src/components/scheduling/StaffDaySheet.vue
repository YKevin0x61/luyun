<script setup>
/**
 * 某一天的「操作单」（票 15）：员工在月历上点一天，就在这一屏里看完那天的事、就地办掉。
 *
 * 为什么把请假与换班收进月历：员工要请假时想的是**哪一天**，不是「从哪天到哪天」；
 * 要换班时想的是**那天谁在班**，不是在一份全店下拉里翻名字。所以这一天是唯一入口 ——
 * 日期从月历上点、人从那天的名单里点，两件事都不用员工自己填。今天页那两颗按钮
 * 改成带 `?date=&act=` 跳到这里，就是为了不再有第二套实现。
 *
 * 数据三条，各管一件事（都只认员工自己的 cookie，接口上没有 `employee_id` 可填）：
 *   · `GET /api/scheduling/me/day?date=`：那天全店谁在班、在哪个区、谁休谁请假；
 *   · `GET|POST|DELETE /api/scheduling/me/requests`：我的申请（看状态、撤回）与「别人问我换班」；
 *   · `GET /api/scheduling/me/colleagues` + `POST /api/scheduling/me/swaps`：换班兜底名单与提交。
 *
 * 判据一律在 `utils/staffDayActions.js`（能不能请、能不能换、能点谁、批了会怎样）——
 * 这一层只摆版式，不在模板里重写第二份判断。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../admin/ConfirmDialog.vue'
import LuyunDatePicker from '../ui/LuyunDatePicker.vue'
import { useNudgePull } from '../../composables/useNudgePull'
import { staffRequest } from '../../utils/hygieneStaff'
import { loginRedirectTarget } from '../../utils/loginNext'
import { dayDiff } from '../../utils/dateRange'
import { canCancel, incomingLine, requestLine, statusTone } from '../../utils/leaveRequest'
import { dayLabel, isMe, myDayText, offLabel, rosterSummary } from '../../utils/todayShift'
import {
  DAY_VIEW,
  actionsNote,
  candidateLine,
  dayActions,
  incomingOnDay,
  requestsOnDay,
  swapCandidates,
  swapPreview,
} from '../../utils/staffDayActions'

const props = defineProps({
  /** 月历上那一格（含 `monthCell` 展开的 text/tone）：日期、我的班、人数都在里面。 */
  day: { type: Object, required: true },
  employee: { type: Object, default: null },
  /** 服务端的营业日与展开窗口末日（「过去」「还没铺到」两条判据拿它们比，不拿手机时间）。 */
  today: { type: String, default: '' },
  windowEnd: { type: String, default: '' },
  /** 从今天页跳过来时直接进的那一屏（'leave' | 'swap' | ''）。 */
  initialAct: { type: String, default: '' },
})
const emit = defineEmits(['close', 'changed'])

const router = useRouter()
const view = ref(DAY_VIEW.day)
// 办完一句回执（提上去了 / 撤回了 / 回应了）。换一屏或换一天就没了 —— 它是这一次动作的结果。
const note = ref('')

function toLogin() {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
}

// ── 那天的名单（我自己拉：这一屏是它唯一的消费者）───────────────────────
const roster = ref(null)
const rosterState = ref('loading') // loading | ready | error
const rosterError = ref('')
const groups = computed(() => (roster.value ? roster.value.groups : []))
const offPeople = computed(() => (roster.value ? roster.value.off_people : []))
const summary = computed(() => (
  roster.value ? rosterSummary(roster.value.total, roster.value.off_count) : ''
))

async function loadRoster(quiet = false) {
  try {
    const data = await staffRequest(
      `/api/scheduling/me/day?date=${encodeURIComponent(props.day.business_date)}`,
    )
    roster.value = data
    rosterState.value = 'ready'
  } catch (err) {
    if (err.status === 401) {
      toLogin()
      return
    }
    // 静默重拉（nudge 触发的）失败不该把已经读出来的名单换成一句错误：留着旧的，下次再说。
    if (quiet) return
    rosterError.value = err.message || '读不到这天的名单'
    rosterState.value = 'error'
  }
}

// ── 我的申请与「别人问我换班」───────────────────────────────────────────
//
// 跟今天页读的是同一条：这一天有没有我的申请、有没有人找我换这天。**今天页与这一屏
// 各读各的**（两边都能撤回 / 回应），谁也不会读到一份过期的副本。
const requests = ref([])
const incoming = ref([])
const requestsError = ref('')
const maxNote = ref(null)
const leaveBusy = ref(false)
const swapBusy = ref(false)

const myToday = computed(() => requestsOnDay(requests.value, props.day.business_date))
const incomingToday = computed(() => incomingOnDay(incoming.value, props.day.business_date))
const noteLabel = computed(
  () => `事由（可空${maxNote.value ? `，最多 ${maxNote.value} 字` : ''}）`,
)

async function loadRequests() {
  try {
    const data = await staffRequest('/api/scheduling/me/requests')
    requests.value = data.requests || []
    incoming.value = data.incoming || []
    if (data.max_request_note) maxNote.value = data.max_request_note
    requestsError.value = ''
  } catch (err) {
    if (err.status === 401) {
      toLogin()
      return
    }
    requestsError.value = err.message || '你的申请读不出来'
  }
}

// ── 这一天能办什么（判据在 util 里）─────────────────────────────────────
const acts = computed(() => dayActions(props.day, {
  today: props.today,
  windowEnd: props.windowEnd,
}))
const whyNote = computed(() => actionsNote(acts.value))
const mineText = computed(() => myDayText(props.day))
// 我那天上什么班（休 / 没班都给空串）：换班预览与「你那天」那行都用它。
const myShiftName = computed(() => (
  props.day && props.day.shift_id != null ? props.day.shift_name || '' : ''
))

// ── 请假那一屏 ──────────────────────────────────────────────────────────
const leaveStart = ref('')
const leaveEnd = ref('')
const leaveNote = ref('')
const leaveError = ref('')
// 「共 N 天」：跨天申请提交前让员工自己核对一眼（少请 / 多请都在这行看出来）。
const leaveDays = computed(() => (
  leaveStart.value && leaveEnd.value && leaveEnd.value >= leaveStart.value
    ? dayDiff(leaveStart.value, leaveEnd.value)
    : 0
))

function startLeave() {
  leaveError.value = ''
  // 起始日就是点开的那一天 —— 员工在月历上点的是哪一天，这里就是哪一天。
  leaveStart.value = props.day.business_date
  leaveEnd.value = ''
  leaveNote.value = ''
  view.value = DAY_VIEW.leave
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
        // 只请一天时后一格空着就不发那个字段（服务端把 None 与空串都当单日）。
        end_date: leaveEnd.value || null,
        note: leaveNote.value || null,
      },
    })
    note.value = '请假提上去了，等店长批'
    view.value = DAY_VIEW.day
    await loadRequests()
    emit('changed')
  } catch (err) {
    if (err.status === 401) {
      toLogin()
      return
    }
    leaveError.value = err.message || '没提交成'
  } finally {
    leaveBusy.value = false
  }
}

// ── 换班那一屏 ──────────────────────────────────────────────────────────
const picked = ref(null)
const swapNote = ref('')
const swapError = ref('')
const colleagues = ref([])
const colleaguesState = ref('idle') // idle | loading | ready | error
const showAll = ref(false)

// 先给**那天在班的人**（点一下就换）；名单里没有的（那天没班 / 新人还没铺到）走下面
// 那条「全部同事」的路 —— 服务端允许跟那天没班的人换（批了他接走你的班）。
const candidates = computed(() => swapCandidates(groups.value, props.employee && props.employee.id))
const allPeople = computed(() => colleagues.value.map((person) => ({
  id: person.id,
  name: person.name,
  zone: '',
  shift_name: '',
  job_title: person.job_title,
})))
const previewLine = computed(() => (
  picked.value ? swapPreview({ shift_name: myShiftName.value }, picked.value) : ''
))

function startSwap() {
  swapError.value = ''
  picked.value = null
  swapNote.value = ''
  showAll.value = false
  view.value = DAY_VIEW.swap
}

/** 名单行尾的「换班」：日期就是这一天、人就是他 —— 进换班那一屏时对象已经点好。
 *  判据仍走 `acts.swap`（过去的日子 / 窗口外 / 我那天请假都不可换），这里不另写一份。 */
function swapWith(group, person) {
  if (!acts.value.swap.ok || !person) return
  startSwap()
  picked.value = {
    id: person.id,
    name: person.name,
    zone: person.zone || '',
    shift_name: (group && group.shift_name) || '',
  }
}

/** 全部同事那份只在真展开时才拉：多数换班就在那天的名单里点完了。 */
async function loadColleagues() {
  if (colleaguesState.value === 'loading' || colleaguesState.value === 'ready') return
  colleaguesState.value = 'loading'
  try {
    const data = await staffRequest('/api/scheduling/me/colleagues')
    colleagues.value = data.colleagues || []
    colleaguesState.value = 'ready'
  } catch (err) {
    if (err.status === 401) {
      toLogin()
      return
    }
    colleaguesState.value = 'error'
    swapError.value = err.message || '同事名单读不出来'
  }
}

function openAll() {
  showAll.value = true
  loadColleagues()
}

function pick(person) {
  picked.value = person
  swapError.value = ''
}

async function submitSwap() {
  if (swapBusy.value || !picked.value) return
  swapBusy.value = true
  swapError.value = ''
  try {
    await staffRequest('/api/scheduling/me/swaps', {
      method: 'POST',
      body: {
        peer_employee_id: Number(picked.value.id) || null,
        business_date: props.day.business_date,
        note: swapNote.value || null,
      },
    })
    note.value = `换班提上去了，等 ${picked.value.name} 同意`
    view.value = DAY_VIEW.day
    await loadRequests()
    emit('changed')
  } catch (err) {
    if (err.status === 401) {
      toLogin()
      return
    }
    swapError.value = err.message || '没提交成'
  } finally {
    swapBusy.value = false
  }
}

// ── 撤回自己的申请 / 回应别人的换班 ────────────────────────────────────
//
// 两处都先过确认框：服务端只往前走（撤回后要重提、拒绝后这件事就结束），手机上一误触
// 没有回头路。同意不弹 —— 后面还有店长那道闸（跟今天页同一条口径）。
const cancelTarget = ref(null)
const answerTarget = ref(null)

async function cancelRequest(request) {
  if (leaveBusy.value) return
  leaveBusy.value = true
  requestsError.value = ''
  let failure = ''
  try {
    await staffRequest(`/api/scheduling/me/requests/${request.id}`, { method: 'DELETE' })
    note.value = '撤回了'
  } catch (err) {
    if (err.status === 401) {
      toLogin()
      return
    }
    failure = err.message || '没撤成'
  } finally {
    leaveBusy.value = false
    // 撤没撤成都要重读：店长可能刚批了 / 驳了（那这条就撤不动了）。「没撤成」那句话
    // 留到重读**之后**再说 —— 读成功会把 `requestsError` 清空，先说等于当场抹掉。
    await loadRequests()
    emit('changed')
    if (failure) requestsError.value = failure
  }
}

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
      toLogin()
      return
    }
    failure = err.message || '没回成'
  } finally {
    swapBusy.value = false
    await loadRequests()
    emit('changed')
    if (failure) requestsError.value = failure
  }
}

async function confirmCancel() {
  const target = cancelTarget.value
  cancelTarget.value = null
  if (target) await cancelRequest(target)
}

async function confirmAnswer() {
  const target = answerTarget.value
  answerTarget.value = null
  if (target) await answerSwap(target.card, target.agree)
}

function close() {
  emit('close')
}

function back() {
  swapError.value = ''
  leaveError.value = ''
  view.value = DAY_VIEW.day
}

function onKeydown(event) {
  if (event.key === 'Escape') close()
}

onMounted(async () => {
  document.addEventListener('keydown', onKeydown)
  // 三条并行：名单、我的申请、（跳过来时）直接进那一屏的判据已经在本地的 props 里。
  await Promise.all([loadRoster(), loadRequests()])
  if (props.initialAct === DAY_VIEW.leave && acts.value.leave.ok) startLeave()
  else if (props.initialAct === DAY_VIEW.swap && acts.value.swap.ok) startSwap()
})

onUnmounted(() => document.removeEventListener('keydown', onKeydown))

// 实时（票 10 收尾）：店长改了这一天、批了我的假，或者有人找我换这天的班 —— 弹层开着
// 的时候这几块都要跟着变（员工正盯着这一屏）。静默重读，不打回「读名单…」。
useNudgePull({
  id: 'staff-day-sheet',
  topics: ['scheduling'],
  pull: () => {
    loadRoster(true)
    loadRequests()
  },
})
</script>

<template>
  <div
    class="modal-overlay"
    role="dialog"
    aria-modal="true"
    aria-labelledby="day-sheet-title"
    @click.self="close"
  >
    <div class="modal-box day-sheet">
      <div class="ds-grab" aria-hidden="true" />
      <!-- ── 这一天 ─────────────────────────────────────────────────── -->
      <template v-if="view === DAY_VIEW.day">
        <div class="modal-header">
          <h3 id="day-sheet-title">{{ dayLabel(day.business_date) }}</h3>
          <button class="btn" type="button" @click="close">关闭</button>
        </div>

        <p v-if="note" class="ds-note">{{ note }}</p>

        <!-- 我这天：格子不写工作区（票 06 口径 1），这一屏是它该出现的地方。 -->
        <p class="rMine">我这天：<b>{{ mineText }}</b></p>
        <p v-if="summary" class="rSum">{{ summary }}</p>

        <!-- 有人找我换这天的班（票 09）：不回应这件事就停在我这里，所以放在最前面。 -->
        <div v-for="card in incomingToday" :key="`in-${card.id}`" class="ds-ask">
          <p class="ds-ask-line">{{ incomingLine(card) }}</p>
          <div class="ds-ask-acts">
            <button
              class="btn"
              type="button"
              :disabled="swapBusy"
              @click="answerSwap(card, true)"
            >同意并转店长</button>
            <button
              class="btn"
              type="button"
              :disabled="swapBusy"
              @click="answerTarget = { card, agree: false }"
            >拒绝</button>
          </div>
        </div>

        <!-- 我在这天提过的申请：状态与撤回都在这一格上，不用去别处找。 -->
        <div v-for="request in myToday" :key="`req-${request.id}`" class="ds-mine">
          <span class="ds-mine-line">{{ requestLine(request) }}</span>
          <span class="ds-mine-tone" :class="statusTone(request.status)" />
          <button
            v-if="canCancel(request)"
            class="btn ds-mini"
            type="button"
            :disabled="leaveBusy"
            @click="cancelTarget = request"
          >撤回</button>
        </div>

        <p v-if="requestsError" class="ds-err" role="alert">{{ requestsError }}</p>

        <!-- 那天的名单（票 13）：点开这一屏就是它该出现的地方。 -->
        <p v-if="rosterState === 'loading'" class="ds-sub">读名单…</p>
        <template v-else-if="rosterState === 'error'">
          <p class="ds-err">{{ rosterError }}</p>
          <button class="btn btn-block" type="button" @click="loadRoster">重试</button>
        </template>
        <template v-else>
          <div v-for="group in groups" :key="group.shift_name" class="rGroup">
            <div class="rHead">
              <span class="rName">{{ group.shift_name }}</span>
              <span class="rCount">{{ group.count }} 人</span>
            </div>
            <ul class="rList">
              <li
                v-for="person in group.people"
                :key="person.id"
                :class="{ me: isMe(person, employee && employee.id) }"
              >
                <span class="rWho">{{ person.name }}</span>
                <span class="rWhere">{{ person.zone || '未配区' }}</span>
                <!-- 行尾「换班」：文字由 CSS 出（::after），不往这一行里塞文本节点 ——
                     这一行的文本口径是「姓名 + 工作区」，测试按整行文本断言。 -->
                <button
                  v-if="!isMe(person, employee && employee.id)"
                  class="r-swap"
                  type="button"
                  :disabled="!acts.swap.ok"
                  :aria-label="`换 ${person.name} 的班`"
                  @click="swapWith(group, person)"
                />
              </li>
            </ul>
          </div>
          <p v-if="!groups.length" class="ds-sub">没人排班</p>

          <div v-if="offPeople.length" class="rGroup">
            <div class="rHead">
              <span class="rName">休假</span>
              <span class="rCount">{{ offPeople.length }} 人</span>
            </div>
            <ul class="rList">
              <li
                v-for="person in offPeople"
                :key="person.id"
                :class="{ me: isMe(person, employee && employee.id) }"
              >
                <span class="rWho">{{ person.name }}</span>
                <!-- 休与请假分开写（票 08 的口径）：判据在 `offLabel` 一处。 -->
                <span class="rWhere" :class="{ leave: person.leave }">{{ offLabel(person) }}</span>
              </li>
            </ul>
          </div>
          <p v-else class="ds-sub">没人休假</p>
        </template>

        <!-- 这一天能办的两件事（B 案 v3）：固定在面板底部拇指区，滚名单时随手就能点。
             日期就是这一天，不用再选。 -->
        <footer class="ds-footer">
          <p class="ds-h">这一天能办</p>
          <div class="ds-acts">
            <button
              class="btn ds-act"
              type="button"
              :disabled="!acts.leave.ok"
              @click="startLeave"
            >请这天的假</button>
            <button
              class="btn ds-act"
              type="button"
              :disabled="!acts.swap.ok"
              @click="startSwap"
            >换这天的班</button>
          </div>
          <p v-if="whyNote" class="ds-why">{{ whyNote }}</p>
        </footer>
      </template>

      <!-- ── 请这天的假 ─────────────────────────────────────────────── -->
      <template v-else-if="view === DAY_VIEW.leave">
        <div class="modal-header">
          <h3>请这天的假</h3>
          <button class="btn" type="button" @click="back">返回</button>
        </div>
        <p class="ds-sub">
          {{ dayLabel(leaveStart || day.business_date) }} 起。只请一天就把后一格空着；
          今天起才能请，最多请到排班铺到的那天。
        </p>
        <div class="form-row">
          <label for="day-leave-start">从哪天</label>
          <!-- 日期用站内的 `LuyunDatePicker`（原生 input 的显示格式跟浏览器界面语言走）。
               `min-date` 用**服务端的营业日**：过去的日子服务端一定会拒（`past_leave`），
               在这里先挡住，别让员工提交了才被打回。 -->
          <LuyunDatePicker
            v-model="leaveStart"
            input-id="day-leave-start"
            :min-date="today || undefined"
            placeholder="选择开始日"
            aria-label="请假从哪天"
          />
        </div>
        <div class="form-row">
          <label for="day-leave-end">到哪天（可空）</label>
          <LuyunDatePicker
            v-model="leaveEnd"
            input-id="day-leave-end"
            :min-date="leaveStart || today || undefined"
            placeholder="只请一天就空着"
            aria-label="请假到哪天"
          />
        </div>
        <p v-if="leaveDays > 1" class="ds-days">共 {{ leaveDays }} 天</p>
        <div class="form-row">
          <label for="day-leave-note">{{ noteLabel }}</label>
          <input
            id="day-leave-note"
            v-model="leaveNote"
            class="input"
            type="text"
            :maxlength="maxNote || undefined"
            placeholder="例如：家里有事"
          >
        </div>
        <p v-if="leaveError" class="ds-err" role="alert">{{ leaveError }}</p>
        <div class="modal-footer">
          <button class="btn" type="button" @click="back">取消</button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="leaveBusy || !leaveStart"
            @click="submitLeave"
          >提交</button>
        </div>
      </template>

      <!-- ── 换这天的班 ─────────────────────────────────────────────── -->
      <template v-else>
        <div class="modal-header">
          <h3>换这天的班</h3>
          <button class="btn" type="button" @click="back">返回</button>
        </div>
        <p class="ds-sub">
          {{ dayLabel(day.business_date) }} · 你那天{{ myShiftName ? ` ${myShiftName}` : '休' }}。
          对方在手机上点同意，店长才看得到这条；两边都点头之后才轮到店长批。
        </p>

        <p class="ds-h">点一个人换</p>
        <p v-if="rosterState === 'loading'" class="ds-sub">读名单…</p>
        <p v-else-if="!candidates.length" class="ds-sub">
          这天没有别人在班，用下面那份全部同事的名单选。
        </p>
        <ul v-else class="ds-people">
          <li v-for="person in candidates" :key="person.id">
            <button
              class="ds-person"
              type="button"
              :class="{ on: picked && picked.id === person.id }"
              :aria-pressed="picked && picked.id === person.id ? 'true' : 'false'"
              @click="pick(person)"
            >
              <span class="ds-person-name">{{ person.name }}</span>
              <span class="ds-person-line">{{ candidateLine(person) }}</span>
              <span class="ds-person-mark" aria-hidden="true">
                {{ picked && picked.id === person.id ? '已选' : '' }}
              </span>
            </button>
          </li>
        </ul>

        <!-- 那天没班的人也得换得成（服务端允许，批了他接走你的班）：展开才拉全店名单。 -->
        <button v-if="!showAll" class="ds-more" type="button" @click="openAll">
          名单里没有？从全部同事里选 ›
        </button>
        <div v-else class="form-row">
          <label for="day-swap-peer">全部同事</label>
          <select
            id="day-swap-peer"
            class="input"
            :disabled="colleaguesState === 'loading'"
            @change="pick(allPeople.find((p) => String(p.id) === $event.target.value) || null)"
          >
            <option value="">选一位同事</option>
            <option v-for="person in allPeople" :key="person.id" :value="person.id">
              {{ person.name }}<template v-if="person.job_title"> · {{ person.job_title }}</template>
            </option>
          </select>
        </div>

        <p v-if="picked" class="ds-preview">{{ previewLine }}</p>

        <div class="form-row">
          <label for="day-swap-note">{{ noteLabel }}</label>
          <input
            id="day-swap-note"
            v-model="swapNote"
            class="input"
            type="text"
            :maxlength="maxNote || undefined"
            placeholder="例如：那天有事，想跟他换个班"
          >
        </div>
        <p v-if="swapError" class="ds-err" role="alert">{{ swapError }}</p>
        <div class="modal-footer">
          <button class="btn" type="button" @click="back">取消</button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="swapBusy || !picked"
            @click="submitSwap"
          >就跟他换</button>
        </div>
      </template>
    </div>

    <!-- 撤回与拒绝先过确认框（同今天页那一处）：两个动作服务端都只往前走。 -->
    <ConfirmDialog
      v-if="cancelTarget"
      title="撤回这条申请"
      message="撤回之后要重新提一次；店长那边这条就消失了。"
      confirm-label="撤回"
      danger
      @confirm="confirmCancel"
      @cancel="cancelTarget = null"
    />
    <ConfirmDialog
      v-if="answerTarget"
      title="拒绝这条换班"
      message="拒绝之后这件事就结束了，对方会看到你拒绝了，也不能再回头同意。"
      confirm-label="拒绝"
      danger
      @confirm="confirmAnswer"
      @cancel="answerTarget = null"
    />
  </div>
</template>

<style scoped>
/* 容器是主题里的 `.modal-overlay/.modal-box`（布局与遮罩都在那儿），这里只管这一屏的
   版式。**组件层只写语义令牌**（`--surface-*` / `--ink-*` / `--action*` / `--warn*` …，
   定义在 `theme.workbench.css` 的基础与语义层）：`--hy-*` 那一层是兼容层，给还在用旧名字的
   存量页面用，新写的文件写它会撞上设计护栏的棘轮（`src/styles/__tests__/designGuard.test.js`）。 */
.day-sheet {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* B 案 v3：底部当日面板 —— 顶部拖拽条（只做视觉提示，拖拽本身不实现）。 */
.ds-grab {
  width: 36px;
  height: 4px;
  flex: none;
  margin: 0 auto 2px;
  border-radius: var(--r-full);
  background: var(--line-strong);
}

/* 按钮统一 ≥48px 方角（面板头那颗「关闭」48×48）。全局 `.btn` 有一条
   `min-height` 40px 基线（!important），这里同权重抬回 48。 */
.day-sheet .btn {
  min-height: 48px !important;
  border-radius: var(--r-1);
}

/* 撤回那颗只要 44px（挨着行，不占主导）；写在通用 48 之后，同分靠后覆盖。 */
.day-sheet .btn.ds-mini {
  min-height: var(--tap) !important;
  padding: 0 14px;
  font-size: 12px;
  flex: none;
}

.day-sheet .modal-header .btn {
  min-width: 48px;
  min-height: 48px !important;
  padding: 0 12px;
}

.day-sheet .modal-header {
  margin-bottom: 0;
}

/* 办完一句回执（提上去了 / 撤回了 / 回应了）：薄荷底，跟错误那句分开。 */
.ds-note {
  margin: 0;
  padding: 7px 10px;
  border: 1px solid var(--action);
  border-radius: var(--r-1);
  background: var(--action-soft);
  color: var(--ok);
  font-size: 12px;
}

.ds-sub {
  margin: 0;
  font-size: 11.5px;
  color: var(--ink-dim);
  line-height: 1.7;
}

.ds-err {
  margin: 0;
  font-size: 11.5px;
  color: var(--danger-bright);
}

/* 这一屏的小标题（「这一天能办」「点一个人换」）。 */
.ds-h {
  margin: 2px 0 0;
  font-size: 10.5px;
  letter-spacing: .12em;
  color: var(--ink-faint);
}

/* ── 有人找我换这天的班 ───────────────────────────────────────────────── */
.ds-ask {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 9px 10px;
  border: 1px solid var(--warn);
  border-radius: var(--r-1);
  background: var(--warn-soft);
}

.ds-ask-line {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
}

.ds-ask-acts {
  display: flex;
  gap: 8px;
}

.ds-ask-acts .btn {
  flex: 1;
}

/* ── 我在这天的申请 ──────────────────────────────────────────────────── */
.ds-mine {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.ds-mine-line {
  flex: 1;
  min-width: 0;
}

/* 状态一枚小色点（语气表在 `utils/leaveRequest.js` 一处）：文本已经在 `requestLine` 里，
   这里只是让「等店长批」与「驳回了」在一屏里看得出不一样。 */
.ds-mine-tone {
  width: 7px;
  height: 7px;
  border-radius: var(--r-full);
  background: var(--ink-faint);
  flex: none;
}

.ds-mine-tone.wait {
  background: var(--warn);
}

.ds-mine-tone.ok {
  background: var(--ok);
}

.ds-mine-tone.no {
  background: var(--danger-bright);
}

/* 撤回这颗要够得着：触控下限 44px（`--tap`）—— 挨着的是不可逆动作。
   两段类名（不是单独一个 `.ds-mini`）：`.hygiene-staff .btn` 也定了 min-height，
   同分特异性下谁后加载谁赢 —— 这一颗要的是 44px，靠多一层选择器赢，不靠顺序。 */
.day-sheet .ds-mini {
  min-height: var(--tap);
  padding: 0 14px;
  font-size: 12px;
  flex: none;
}

/* ── 这一天能办的两件事 ───────────────────────────────────────────────── */
/* 底部拇指区（B 案 v3）：「请这天的假 / 换这天的班」钉在面板底部，滚名单时随手可点。
   面板本身是 `overflow-y:auto` 的滚动容器，sticky bottom 在内容末尾生效。 */
.ds-footer {
  position: sticky;
  bottom: 0;
  z-index: var(--z-shell);
  margin: 12px -18px 0;
  padding: 10px 18px 4px;
  background: var(--surface-1);
  border-top: 1px solid var(--line);
}

.ds-footer .ds-h {
  margin: 0 0 7px;
}

.ds-acts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.ds-act {
  width: 100%;
}

.ds-why {
  margin: 0;
  font-size: 11px;
  color: var(--ink-faint);
  line-height: 1.6;
}

.ds-days {
  margin: 0 0 10px;
  font-size: 11.5px;
  color: var(--ok);
}

/* ── 换班：可点的人 ───────────────────────────────────────────────────── */
.ds-people {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* 一行一个人：名字 + 他那天的班次与区。整行可点（手机上比勾选框好点）。 */
.ds-person {
  width: 100%;
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 9px 11px;
  min-height: 46px;
  font: inherit;
  text-align: left;
  color: inherit;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  cursor: pointer;
}

.ds-person.on {
  border-color: var(--action);
  background: var(--action-soft);
}

.ds-person-name {
  font-size: 13px;
}

.ds-person-line {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--ink-dim);
}

.ds-person-mark {
  font-size: 10.5px;
  color: var(--ok);
  flex: none;
}

.ds-more {
  align-self: flex-start;
  font: inherit;
  font-size: 11.5px;
  color: var(--ink-dim);
  background: none;
  border: 0;
  padding: 4px 0;
  cursor: pointer;
}

/* 选中之后那句「批了会怎样」：换班这一屏最要紧的一句，给它自己的分量。 */
.ds-preview {
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--r-1);
  background: var(--surface-3);
  font-size: 12px;
  line-height: 1.6;
}

/* ── 那天的名单 ──────────────────────────────────────────────────────── */
.rMine {
  margin: 0;
  font-size: 12.5px;
  color: var(--ink-dim);
}

.rMine b {
  font-size: 14px;
  letter-spacing: .02em;
  color: var(--ok);
}

.rSum {
  margin: 0;
  font-size: 11px;
  color: var(--ink-faint);
}

.rGroup {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rHead {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 3px;
  border-bottom: 1px solid var(--line);
}

.rHead .rName {
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: .04em;
}

.rHead .rCount {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--ink-faint);
}

.rList {
  list-style: none;
  margin: 0;
  padding: 0;
}

.rList li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 44px;
  padding: 0 2px;
  font-size: 12.5px;
  border-bottom: 1px dashed var(--line);
}

.rList li:last-child {
  border-bottom: 0;
}

/* 我在名单里的那一行：薄荷底 + 名字后面一枚「我」—— 按 id 认（`isMe`），不按名字。 */
.rList li.me {
  background: var(--action-soft);
  border-radius: var(--r-1);
  padding-left: 6px;
  padding-right: 6px;
}

.rList li.me .rWho::after {
  content: '我';
  margin-left: 6px;
  font-size: 9.5px;
  color: var(--ok);
  border: 1px solid var(--action);
  border-radius: var(--r-full);
  padding: 0 4px;
}

.rWhere {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--ink-dim);
}

/* 名单行尾的「换班」入口：文字由 CSS 出（不往这一行塞文本节点，整行文本口径不变）。
   触控 44×44；那一天能不能换仍由 `acts.swap` 判（过去 / 窗口外 / 我那天请假）。 */
.r-swap {
  flex: none;
  margin-left: 8px;
  min-width: 44px;
  min-height: 44px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: transparent;
  color: var(--action);
  font-family: var(--font-sans);
  font-size: 11px;
  cursor: pointer;
}

.r-swap::after {
  content: "换班";
}

.r-swap:disabled {
  opacity: .4;
  cursor: default;
}

/* 请假用青色（跟月历格子里那一态同色），休仍是灰的 —— 两件事两个颜色。 */
.rWhere.leave {
  color: var(--info);
}
</style>
