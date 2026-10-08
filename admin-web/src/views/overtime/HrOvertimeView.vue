<script setup>
/**
 * 管理端的「加班统计」页（票 02）：一个审批台 + 一份台账。
 *
 * 上面那一块是**等审批**：员工在手机上提的、店长在手机上没批的，全在这里；批准、驳回
 * （驳回要写理由，理由会出现在员工手机上）。中间那块是**代录** —— 员工忘了登、或者窗口
 * 已经过了的时候，超管替他补一笔任意过去日期；补录走的是同一条流水线，所以补完它还是
 * 「待审批」，得再点一次批准才算数（`docs/adr/0100`：补录是逃生口，不是第二套流程）。
 * 最下面那块是**台账**：按月看全部登记，已经批准的那几笔在这里能**作废**（批准之后员工
 * 动不了它，填错了、批错了只有这一条路能救），作废不删记录。
 *
 * 两件这一页**不做**的事：不算钱（底薪快照与加班费在票 03），不给店长用（店长没有管理端
 * 账号，他在员工手机上批，见票 04）。所以这一页的响应里没有金额，也不该有。
 *
 * 实时：员工提了 / 撤了，或者另一个标签页批过，队列跟着变（nudge + pull，topic `overtime`）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { api } from '../../api/client'
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

const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
const receipt = ref('')
const today = ref('')
const queue = ref([]) // 等审批（旧的在前）
const entries = ref([]) // 台账（新的在前，可按月筛）
const employees = ref([])
const month = ref('') // '' = 不分月
const busyId = ref(0) // 行内动作的单槽（同排班待办页）
const formBusy = ref(false)
const rejectTarget = ref(null)
const rejectReason = ref('')
const voidTarget = ref(null)

// 上限随响应下发（`/admin/pending` 带 `max_*`）：页面不写死第二份 50 / 12 / 100，
// 服务端改口径时这里跟着走（同员工端那一页读 `max_half_hours` 的写法）。
const maxReason = ref(MAX_REASON)
const maxHalfHours = ref(HALF_HOURS_MAX)
const maxRejectReason = ref(MAX_REJECT_REASON)

const form = ref({ employee_id: '', entry_date: '', half_hours: 1, reason: '' })
const formError = ref('')

const canBackfill = computed(
  () =>
    !!form.value.employee_id &&
    !!form.value.entry_date &&
    !!form.value.reason.trim() &&
    !formBusy.value,
)
// 停用的人不进下拉（`/admin/employees` 把 `disabled` 带下来了）：历史登记还能补在他头上，
// 但补录表单是给「现在还在上班的人」用的。已经提上来的那些不受影响。
const pickable = computed(() => employees.value.filter((person) => !person.disabled))

function employeeName(id) {
  const found = employees.value.find((person) => person.id === id)
  return found ? found.name : ''
}

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    const params = month.value ? { month: month.value } : undefined
    const [pendingData, listing, people] = await Promise.all([
      api.get('/api/overtime/admin/pending'),
      api.get('/api/overtime/admin/entries', params),
      api.get('/api/overtime/admin/employees'),
    ])
    today.value = pendingData.today || ''
    queue.value = pendingData.entries || []
    entries.value = listing.entries || []
    employees.value = people.employees || []
    maxReason.value = pendingData.max_reason || MAX_REASON
    maxHalfHours.value = pendingData.max_half_hours || HALF_HOURS_MAX
    maxRejectReason.value = pendingData.max_reject_reason || MAX_REJECT_REASON
    if (!form.value.entry_date) form.value.entry_date = today.value
    state.value = 'ready'
  } catch (err) {
    errorText.value = err.message || '读不出加班台账'
    state.value = 'error'
  }
}

/** 写失败：先把服务端那句话留住，重读台账，**最后**再说出来。
 *
 *  顺序不能反：`load()` 开头会把 `errorText` 清掉，先写后读等于把「这条已经处理过了」
 *  当场抹掉 —— 点完批准什么都没看到，会以为没批成而反复点（同排班待办页的口径）。
 */
async function reportFailure(err, fallback) {
  const message = err.message || fallback
  await load()
  errorText.value = message
}

const RECEIPTS = {
  approve: (entry) =>
    `批了 ${entry.employee_name || '这一笔'} 的${entryKindText(entry.kind)}`,
  reject: (entry) =>
    `驳回了 ${entry.employee_name || '这一笔'} 的${entryKindText(entry.kind)}，理由员工看得到`,
  void: (entry) =>
    `作废了 ${entry.employee_name || '这一笔'} 的${entryKindText(entry.kind)}：记录没删，状态是已作废`,
}

async function decide(entry, action, body = {}) {
  // 单槽挡住重入：两条同时在飞时，先回来的那条会在 finally 里把槽清零，后一条的按钮
  // 就全部恢复可点、能对同一条再发一次（同排班待办页的注释）。
  if (busyId.value) return
  busyId.value = entry.id
  errorText.value = ''
  receipt.value = ''
  try {
    await api.post(`/api/overtime/admin/entries/${entry.id}/${action}`, body)
    receipt.value = (RECEIPTS[action] || (() => '处理好了'))(entry)
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
  if (entry) await decide(entry, 'reject', { reason: rejectReason.value.trim() })
}

async function confirmVoid() {
  const entry = voidTarget.value
  voidTarget.value = null
  if (entry) await decide(entry, 'void')
}

async function loadListing() {
  try {
    const params = month.value ? { month: month.value } : undefined
    const listing = await api.get('/api/overtime/admin/entries', params)
    entries.value = listing.entries || []
  } catch (err) {
    errorText.value = err.message || '读不出这个月的台账'
  }
}

async function backfill() {
  if (formBusy.value) return
  formBusy.value = true
  formError.value = ''
  receipt.value = ''
  const who = employeeName(form.value.employee_id)
  try {
    await api.post('/api/overtime/admin/entries', {
      employee_id: Number(form.value.employee_id),
      entry_date: form.value.entry_date,
      half_hours: form.value.half_hours,
      reason: form.value.reason.trim(),
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
  document.title = workbenchDocumentTitle('加班统计')
  load()
})

// 这一页本来就是「等别人动作」的地方：员工提了、撤了，队列要跟着变。
useNudgePull({ id: 'workbench-hr-overtime', topics: ['overtime'], pull: load })
</script>

<template>
  <div class="hygiene-admin overtime-admin">
    <header class="oTop">
      <div>
        <p class="oKicker">人事</p>
        <h1>加班统计</h1>
        <p class="oSub">
          <template v-if="today">今天 {{ today }} · </template>
          员工在手机上提的加班与补钟都在这里批；只有批过的才算数。
        </p>
      </div>
      <button class="btn" type="button" @click="router.push(WORKBENCH_HR_HOME)">回到月历</button>
    </header>

    <p v-if="state === 'loading'" class="oHint">正在读加班台账…</p>

    <template v-else>
      <p v-if="errorText" class="oErr" role="alert">{{ errorText }}</p>
      <p v-else-if="receipt" class="oOk" role="status">{{ receipt }}</p>

      <section class="oCard">
        <div class="oCard-hd">
          <h2>等审批</h2>
          <span class="oCount">{{ queue.length }}</span>
        </div>
        <p v-if="!queue.length" class="oEmpty">
          没有等着批的登记。员工提了、或者你刚才补录了，都会立刻出现在这里。
        </p>
        <ul v-else class="oList">
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
      </section>

      <section class="oCard">
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
            <input v-model="form.entry_date" class="oInput" type="date" :max="today" />
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
              placeholder="这笔是干什么的（必填）"
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

      <section class="oCard">
        <div class="oCard-hd">
          <h2>台账</h2>
          <span class="oCount">{{ entries.length }}</span>
          <input v-model="month" class="oInput oMonth" type="month" @change="loadListing" />
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
                @click="voidTarget = entry"
              >
                作废
              </button>
            </div>
          </li>
        </ul>
      </section>
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
/* 版式照排班待办页：深青墨底 + 一张卡一个话题。令牌来自 /hygiene-admin.css（壳一层加载），
   `.btn` 那一族也在那儿。 */
.overtime-admin { padding: 18px 16px 40px; max-width: 780px; margin: 0 auto; }
.oTop { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
.oTop h1 { margin: 2px 0 4px; font-size: 20px; color: var(--hy-ink); }
.oKicker { margin: 0; font-size: 11px; letter-spacing: 0.18em; color: var(--hy-faint); }
.oSub { margin: 0; font-size: 12px; line-height: 1.7; color: var(--hy-muted); }
.oTop .btn { margin-left: auto; flex: none; }
.oHint { margin: 8px 0 0; font-size: 12px; line-height: 1.7; color: var(--hy-muted); }
.oErr {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--hy-radius-md);
  border: 1px solid rgba(255, 138, 128, 0.35); background: rgba(255, 138, 128, 0.12);
  font-size: 12.5px; color: var(--hy-coral);
}
.oOk {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--hy-radius-md);
  border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
  font-size: 12.5px; color: var(--hy-mint);
}
.oCard {
  margin-bottom: 14px; padding: 14px; border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-lg); background: var(--hy-surface);
}
.oCard-hd { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.oCard-hd h2 { margin: 0; font-size: 14px; color: var(--hy-ink); }
.oCount {
  min-width: 20px; padding: 1px 7px; border-radius: 999px; text-align: center;
  font-family: var(--font-mono); font-size: 11.5px; color: var(--hy-night);
  background: var(--hy-amber);
}
.oMonth { margin-left: auto; max-width: 150px; }
.oLead { margin: 0 0 10px; font-size: 12px; line-height: 1.7; color: var(--hy-muted); }
.oEmpty { margin: 4px 0 0; font-size: 12.5px; color: var(--hy-faint); }
.oList { list-style: none; margin: 0; padding: 0; }
.oItem { padding: 12px 0; border-top: 1px solid var(--hy-line); }
.oItem:first-child { border-top: 0; padding-top: 6px; }
.oRow-hd { display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap; }
.oRow-hd b { font-size: 14px; color: var(--hy-ink); }
.oDate { font-family: var(--font-mono); font-size: 12.5px; color: var(--hy-muted); }
.oH { font-size: 15px; color: var(--hy-ink); font-variant-numeric: tabular-nums; }
.oH.minus { color: var(--hy-coral); }
.oKind {
  padding: 1px 8px; border-radius: 999px; font-size: 11px;
  color: var(--hy-aqua); border: 1px solid var(--hy-aqua); background: rgba(94, 234, 212, 0.1);
}
.oPend { margin-left: auto; font-size: 11.5px; color: var(--hy-amber); }
.oSt { margin-left: auto; font-size: 11.5px; }
.oSt.wait { color: var(--hy-amber); }
.oSt.ok { color: var(--hy-mint); }
.oSt.bad { color: var(--hy-coral); }
.oSt.mute { color: var(--hy-faint); }
.oReason { margin: 6px 0 0; font-size: 13px; line-height: 1.6; color: var(--hy-ink); }
.oBy { margin: 4px 0 0; font-size: 11.5px; color: var(--hy-faint); }
.oAct { display: flex; gap: 8px; margin-top: 10px; }
.oReject { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.oReject .oInput { flex: 1 1 240px; }
.oForm { display: flex; flex-direction: column; gap: 10px; }
.oField { display: flex; align-items: center; gap: 10px; }
.oLabel { width: 52px; flex: none; font-size: 12.5px; color: var(--hy-muted); }
.oInput {
  padding: 7px 10px; border: 1px solid var(--hy-line); border-radius: var(--hy-radius-md);
  background: transparent; font-size: 13px; color: var(--hy-ink);
}
.oField .oInput { flex: 1; }
.oStepper { display: flex; align-items: center; gap: 10px; }
.oStep {
  width: 32px; height: 32px; border: 1px solid var(--hy-line); border-radius: 50%;
  background: transparent; font-size: 17px; line-height: 1; color: var(--hy-ink);
}
.oHours {
  min-width: 92px; text-align: center; font-size: 15px; color: var(--hy-ink);
  font-variant-numeric: tabular-nums;
}
.oHours.minus { color: var(--hy-coral); }
.oForm > .btn { align-self: flex-start; }
</style>
