<script setup>
// 超级管理员端「排班 · 待办」（票 08 请假、票 09 换班）：等超级管理员批的申请，
// 加一份「谁还没配规则」的提醒。
//
// 申请是员工在手机上提的（`POST /api/scheduling/me/requests`、`/me/swaps`），审批落在
// 超级管理员手里：这一页就是那张审批台。每条申请**先把结果摊开再让超级管理员按**（票 08 的验收 2）：
// 请假批了之后那天每个班次还剩几个人，一行一天写清楚；换班批了两个人那天的班怎么对调，
// 一行一天写清楚。已经过去的日子明说「批了也不改历史」。
// 人手够不够只提示、不拦（服务层没有「最少几个人」这个配置，也就没有那道闸）。
// 换班比请假多一道门：**对方先同意**，那条才会出现在这里（服务层的 `pending_peer`）。
//
// 视觉沿用 `public/hygiene-admin.css` 的深青墨令牌 —— 那是**共享的样式表**，不是卫生
// 模块：排班不 import 卫生的 Python 模块、不挂它的菜单，只是同一套验收台配色。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { api } from '../../api/client'
import { useNudgePull } from '../../composables/useNudgePull'
import { WORKBENCH_HR_HOME, workbenchDocumentTitle } from '../../utils/workbenchCopy'
// 共享样式表由壳加载（`SchedulingLayout.vue`）：三个子页各加载一份会挂出重复的
// <link>，壳一层管住就跟卫生管理端一个做法。
import {
  approveReceipt,
  kindText,
  noteText,
  previewLine,
  requestRangeText,
  shortDay,
  splitRuleless,
  swapApproveReceipt,
  swapPreviewLine,
} from '../../utils/leaveRequest'


const router = useRouter()

const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
const today = ref('')
const requests = ref([])
const withoutRule = ref([])
const busyId = ref(0)
// 批完/驳完的一句回执。不放进 errorText：它不是错误，但两者都在同一块地方显示，
// 谁最后发生谁说话（这里只在动作成功后写，出错时 errorText 会顶掉它）。
const receipt = ref('')
const rejectTarget = ref(null)
// 批准也要先问一句：批错了没有撤回按钮，员工已经看到「批了」，复原得去月历把那天
// 的班次一格一格改回来。驳回本来就有确认，闸要加在同一侧（见下方 `approveMessage`）。
const approveTarget = ref(null)

// 「要提醒的」与「不提醒的」两拨新人（口径见 `utils/leaveRequest.js` 的 `splitRuleless`）。
const ruleless = computed(() => splitRuleless(withoutRule.value))

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    const data = await api.get('/api/scheduling/inbox')
    today.value = data.today || ''
    requests.value = data.requests || []
    withoutRule.value = data.without_rule || []
    state.value = 'ready'
  } catch (err) {
    errorText.value = err.message || '待办读不出来'
    state.value = 'error'
  }
}

/** 写失败：先把服务端那句话留住，重读待办，**最后**再说出来。
 *
 *  顺序不能反：`load()` 开头会把 `errorText` 清掉（那是给进页面 / 重读用的），先写后读
 *  等于把「这条申请已经处理过了」这句话当场抹掉 —— 超级管理员点完「批准」什么都看不到
 *  （既没有错误也没有回执），会以为没批成而反复点。跟班次页 `reportFailure` 同一个口径。
 */
async function reportFailure(err, fallback) {
  const message = err.message || fallback
  await load()
  errorText.value = message
}

async function decide(request, action) {
  // 单槽 busyId 表示「有动作在飞」，所以这里必须挡住重入：放两条同时飞，先回来的
  // 那条会在 `finally` 里把槽清零，后一条的按钮就全部恢复可点、能对同一条再发一次；
  // 后回来的回执还会顶掉前一条（回执说的是 A，而 A 已经从列表上消失了）。服务端的
  // `request_not_pending` 挡得住数据被改坏，挡不住超级管理员看到「点了没反应 / 回执对不上」。
  if (busyId.value) return
  busyId.value = request.id
  errorText.value = ''
  receipt.value = ''
  try {
    const data = await api.post(`/api/scheduling/inbox/${request.id}/${action}`, {})
    if (action === 'approve') {
      receipt.value = request.kind === 'swap' ? swapApproveReceipt(data) : approveReceipt(data)
    } else {
      receipt.value = `${request.employee_name} 的${kindText(request.kind)}驳回了，排班没动`
    }
    await load()
  } catch (err) {
    // 可能是别人先批了/员工自己撤了（`request_not_pending`）：重读一次待办，
    // 别让超级管理员对着一条已经不存在的申请反复点 —— 服务端那句话留到最后说。
    await reportFailure(err, '没处理成')
  } finally {
    // 只清自己占的那个槽：不是本次请求的 `finally` 不许把在飞那条的保护撤掉。
    if (busyId.value === request.id) busyId.value = 0
  }
}

async function confirmReject() {
  const target = rejectTarget.value
  rejectTarget.value = null
  if (target) await decide(target, 'reject')
}

/** 「批准」确认框里那句「批了会怎样」：从卡片第二行已经摊开的预览数据里挑一天说。
 *
 *  **不能把整段预览搬进框里** —— 那正是卡片第二行的内容，框里再抄一遍既长又难读。
 *  所以只说挑出来的那一天（同一天多个班次就说全，人头的判据与 `previewLine` 一致），
 *  跨天的补一句「共 N 天」。已经过去的日子不算：批了它也不动排班，说了反而误导。
 *  没有任何可用预览数据时退一句通用的，**绝不空着**（空话比不弹框更糟）。
 *  换班照 `swapPreviewLine` 的三种情况说：对调 / 一方接走 / 都没班。
 */
function approveMessage(request) {
  const days = (request && request.days) || []
  const live = days.filter((day) => day && !day.past)
  if (!live.length) {
    return days.length
      ? '这几天都已经过去：批了只记成已批准，排班不动。'
      : '批了就写进排班，员工会收到通知。'
  }
  const day = live[0]
  const when = shortDay(day.business_date)
  const more = live.length > 1 ? `（共 ${live.length} 天）` : ''
  if (request.kind === 'swap') {
    const mine = day.current_shift_name || ''
    const theirs = day.peer_scheduled ? day.peer_shift_name || '班次已调整' : ''
    if (mine && theirs) return `批了之后 ${when} 两个人的班对调：${mine} ⇄ ${theirs}${more}`
    if (mine) return `批了之后 ${when} 对方接你的${mine}${more}`
    if (theirs) return `批了之后 ${when} 你接对方的${theirs}${more}`
    return `批了之后 ${when} 两个人都没有班，排班不动${more}`
  }
  const left = (day.after || []).map((item) => `${item.shift_name}剩 ${item.count} 人`).join('、')
  if (left) return `批了之后 ${when} ${left}${more}`
  if (!day.scheduled) return `批了之后 ${when} 没有你的班，排班不动${more}`
  return day.current_shift_name
    ? `批了之后 ${when} 空出${day.current_shift_name}${more}`
    : `批了之后 ${when} 那天本来就休，排班不动${more}`
}

async function confirmApprove() {
  const request = approveTarget.value
  approveTarget.value = null
  if (request) await decide(request, 'approve')
}

onMounted(() => {
  // 名字与清单里 `/workbench/hr/inbox` 那一行同步，后缀走 `workbenchDocumentTitle`（同上）。
  document.title = workbenchDocumentTitle('排班待办')
  load()
})

// 实时（票 10 收尾）：员工提了新的申请、撤回了、或者对方回了话 —— 待办列表重拉一次。
// 这一页本来就是「等别人动作」的地方，没有实时就只能靠人反复刷新。
useNudgePull({ id: 'workbench-inbox', topics: ['scheduling'], pull: load })
</script>

<template>
  <div class="hygiene-admin inbox-page">
    <header class="iTop">
      <p class="iKicker">人事 · 排班</p>
      <div class="iTitleRow">
        <h1>待办</h1>
        <button class="btn iAction" type="button" @click="router.push(WORKBENCH_HR_HOME)">回到月历</button>
      </div>
      <div class="iSubRow">
        <p class="iSub">
          <template v-if="today">今天 {{ today }} · </template>请假与换班都在这里批；批完那天就记成请假 / 对调。
        </p>
        <!-- 页头只放要动手的未处理数（零值不显示）；申请条数在面板头，不重复。 -->
        <button
          v-if="ruleless.active.length"
          class="iChip"
          type="button"
          @click="router.push(WORKBENCH_HR_HOME)"
        >未配规则 <b>{{ ruleless.active.length }}</b> 人 ›</button>
      </div>
    </header>

    <p v-if="state === 'loading'" class="iHint">正在读待办…</p>

    <!-- 读失败**独占内容区**：原来错误只占一行、下面照常渲染「没有等着批的申请」，
         故障被读成「今天没假条」是漏批的直接原因。 -->
    <section v-else-if="state === 'error'" class="iFail">
      <p class="iErr" role="alert">{{ errorText }}</p>
      <button class="btn" type="button" @click="load()">重试</button>
    </section>

    <template v-else>
      <p v-if="receipt" class="iOk" role="status">{{ receipt }}</p>
      <p v-if="errorText" class="iErr" role="alert">{{ errorText }}</p>

      <section class="iCard">
        <div class="iCard-hd">
          <i class="iLed warn"></i>
          <h2>申请等着批</h2>
          <span class="iHint">{{ requests.length }} 条</span>
        </div>
        <p v-if="!requests.length" class="iEmpty">
          没有等着批的申请。员工在手机上提了请假会出现在这里；换班要对方先点同意，才会轮到这儿。
        </p>
        <ul v-else class="iList">
          <li v-for="request in requests" :key="request.id" class="iItem">
            <div class="iItem-hd">
              <span class="iKind">{{ kindText(request.kind) }}</span>
              <b>{{ request.employee_name || '（没有名字）' }}</b>
              <span v-if="request.kind === 'swap'" class="iPeer">⇄ {{ request.peer_name || '（对方）' }}</span>
              <span class="iWhen">{{ requestRangeText(request.start_date, request.end_date) }}</span>
              <span class="iDays">{{ (request.days || []).length }} 天</span>
              <span class="iPend"><i class="iLed warn"></i>等你批</span>
              <div class="iAct">
                <button
                  class="btn btn-primary"
                  type="button"
                  :disabled="busyId !== 0"
                  @click="approveTarget = request"
                >批准</button>
                <button
                  class="btn btn-danger"
                  type="button"
                  :disabled="busyId !== 0"
                  @click="rejectTarget = request"
                >驳回</button>
              </div>
            </div>
            <div class="iItem-bd">
              <p v-if="noteText(request.note)" class="iNote">{{ noteText(request.note) }}</p>
              <ul class="iPrev">
                <li
                  v-for="day in request.days"
                  :key="day.business_date"
                  :class="{ past: day.past, none: !day.scheduled }"
                >{{ request.kind === 'swap'
                  ? swapPreviewLine(day, request.employee_name, request.peer_name)
                  : previewLine(day) }}</li>
              </ul>
            </div>
          </li>
        </ul>
      </section>

      <!-- 未配规则：页头胶囊只给数字，完整名单折在这一行里（展开才占地方）。 -->
      <details v-if="ruleless.active.length" class="iRuleless">
        <summary>还没配规则的人 {{ ruleless.active.length }} 人 ›</summary>
        <div class="iRuleless-bd">
          <p class="iLead">
            没配规则就不会被排班，请假提了也没班可改。名单上停用或没批准的人不在这儿提醒。
          </p>
          <ul class="iPeople">
            <li v-for="person in ruleless.active" :key="person.id">
              <b>{{ person.name }}</b>
              <span>{{ person.job_title || '员工' }}</span>
              <span v-if="person.phone" class="iPhone">{{ person.phone }}</span>
            </li>
          </ul>
          <p v-if="ruleless.muted.length" class="iMuted">
            另有 {{ ruleless.muted.length }} 个人已停用或还没批准，不提醒。
          </p>
          <div class="iSide-act">
            <button class="btn" type="button" @click="router.push(WORKBENCH_HR_HOME)">去配固定班</button>
          </div>
        </div>
      </details>
    </template>

    <ConfirmDialog
      v-if="approveTarget"
      :title="`批准这次${kindText(approveTarget.kind)}`"
      :message="approveMessage(approveTarget)"
      confirm-label="批准"
      @confirm="confirmApprove"
      @cancel="approveTarget = null"
    />

    <ConfirmDialog
      v-if="rejectTarget"
      :title="`驳回这次${kindText(rejectTarget.kind)}`"
      :message="`驳回「${rejectTarget.employee_name || '这位员工'}」的${kindText(rejectTarget.kind)}后，这条申请就结束了（员工看得到）。排班一个字不改。`"
      confirm-label="驳回"
      danger
      @confirm="confirmReject"
      @cancel="rejectTarget = null"
    />
  </div>
</template>

<style scoped>
/* B 案 v3（2026-10-17 版式审查）：1052 容器、页头 3 行、一页一块面板；申请卡压成
   两行（类型 · 姓名 · 日期 · 状态 · 动作 / 影响说明），右栏「未配规则」降级成页头胶囊
   + 一行折叠。页头胶囊只放要动手的未处理数，零值不显示。 */
.iFail {
  display: flex; flex-direction: column; gap: 12px; align-items: flex-start;
  padding: 16px 18px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
}
.inbox-page {
  display: flex; flex-direction: column; gap: var(--sp-4);
  padding: 18px clamp(16px, 4vw, 32px) calc(40px + var(--safe-b));
  width: 100%; max-width: 1052px; margin: 0 auto; font-family: var(--font-sans);
}
.iTop {
  display: grid; grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "kick kick" "title action" "sub chip";
  align-items: end; column-gap: var(--sp-4); row-gap: 7px;
}
.iKicker {
  grid-area: kick; margin: 0; font-family: var(--font-sans); font-size: 9.5px; line-height: 1;
  font-weight: 500; letter-spacing: .26em; color: var(--ink-faint);
}
.iTitleRow { grid-area: title; display: flex; align-items: baseline; gap: var(--sp-4); min-width: 0; }
.iTop h1 {
  margin: 0; font-family: var(--font-sans); font-size: 26px; line-height: 1;
  font-weight: 600; letter-spacing: .01em; color: var(--ink);
}
.iAction { margin-left: auto; flex: none; border-radius: var(--r-1); min-height: var(--tap); }
.iSubRow { grid-area: sub; display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; min-width: 0; }
.iSub { margin: 0; max-width: 720px; font-size: var(--fs-12); line-height: 1.6; color: var(--ink-dim); }
/* 页头胶囊：只放「要动手的未处理数」，零值不渲染。 */
.iChip {
  grid-area: chip; margin-left: auto; display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 10px; border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: var(--surface-1); color: var(--warn); cursor: pointer;
  font-family: var(--font-mono); font-size: 10px; line-height: 1;
}
.iChip b { color: var(--ink); font-weight: 500; }
.iLed { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; }
.iLed.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.iHint { margin: 0; font-size: 12.5px; color: var(--ink-dim); }
.iErr {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--wb-danger-a45); background: var(--danger-soft);
  font-size: 12.5px; color: var(--danger-bright);
}
.iOk {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--line-strong); background: var(--ok-soft);
  font-size: 12.5px; color: var(--ok);
}
/* 唯一的主面板：头 + 行。 */
.iCard {
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: var(--hy-shadow-sm); overflow: hidden;
}
.iCard-hd {
  display: flex; align-items: center; gap: 9px;
  padding: 11px 14px; border-bottom: 1px solid var(--line);
}
.iCard-hd h2 {
  margin: 0; font-family: var(--font-sans); font-size: 12.5px; line-height: 1;
  font-weight: 600; letter-spacing: .06em; color: var(--ink);
}
.iCard-hd .iHint {
  margin-left: auto; font-family: var(--font-mono); font-size: 9.5px; line-height: 1;
  color: var(--ink-faint);
}
.iEmpty { margin: 0; padding: 14px; font-size: 12px; color: var(--ink-faint); }
.iList { list-style: none; margin: 0; padding: 0; }
/* 一条申请 = 两行：类型 / 姓名 / 日期 / 状态 / 动作（右对齐）；第二行影响说明。 */
.iItem {
  padding: 10px 14px; border-top: 1px solid var(--wb-ink-a08);
}
.iItem:first-child { border-top: 0; }
.iItem:hover { background: var(--surface-2); }
.iItem-hd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-height: 44px; }
.iItem-hd b { min-width: 0; font-size: 13px; font-weight: 600; color: var(--ink); }
.iKind {
  font-family: var(--font-sans); font-size: 8.5px; line-height: 1;
  font-weight: 500; letter-spacing: .2em; color: var(--info);
}
.iWhen { font-family: var(--font-mono); font-size: 11px; color: var(--ink); }
.iDays { font-family: var(--font-mono); font-size: 10px; color: var(--ink-faint); }
.iPeer { font-size: var(--fs-12); color: var(--hy-amber); }
.iPend {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 9px; letter-spacing: .06em; color: var(--warn); white-space: nowrap;
}
.iAct { margin-left: auto; display: flex; gap: var(--sp-2); }
.iAct .btn { border-radius: var(--r-1); min-height: var(--tap); }
.iItem-bd { display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; margin-top: 2px; }
.iNote {
  margin: 0; padding-left: 9px; border-left: 2px solid var(--line-strong);
  font-size: 10.5px; line-height: 1.6; color: var(--ink-dim);
}
.iPrev { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 2px 14px; }
.iPrev li {
  display: flex; align-items: center; padding-left: 9px;
  border-left: 2px solid var(--line-strong);
  font-family: var(--font-mono); font-size: 10px; line-height: 1.5; color: var(--ink);
}
.iPrev li:first-child { border-left-color: var(--action); }
.iPrev li.past { color: var(--ink-faint); border-left-color: var(--line); }
.iPrev li.none { color: var(--ink-dim); border-left-color: var(--line-strong); }
/* 未配规则：一行折叠入口，展开才出名单。 */
.iRuleless {
  border: 1px solid var(--line); border-radius: var(--r-1); background: var(--surface-1);
}
.iRuleless summary {
  display: flex; align-items: center; gap: 8px; min-height: 48px; padding: 0 14px;
  cursor: pointer; font-size: 11px; color: var(--ink-dim);
  font-family: var(--font-mono);
}
.iRuleless summary::before {
  content: ""; width: 6px; height: 6px; flex: none; border-radius: 50%; background: var(--warn);
}
.iRuleless-bd { padding: 0 14px 14px; }
.iLead { margin: 0 0 8px; font-size: 11px; line-height: 1.7; color: var(--ink-dim); }
.iMuted { margin: 8px 0 0; font-size: 10.5px; color: var(--ink-faint); }
.iPeople { list-style: none; margin: 0; padding: 0; }
.iPeople li {
  display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center;
  gap: 10px; min-height: 44px; border-top: 1px solid var(--wb-ink-a08);
  font-size: 12px; color: var(--ink-dim);
}
.iPeople li:first-child { border-top: 0; }
.iPeople b { min-width: 0; font-size: 12.5px; font-weight: 600; color: var(--ink); }
.iPhone { font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-faint); }
.iSide-act { padding-top: 10px; }
.iSide-act .btn { border-radius: var(--r-1); min-height: var(--tap); }
@media (max-width: 900px) {
  .iTop { grid-template-columns: 1fr; grid-template-areas: "kick" "title" "sub"; }
  .iChip { margin-left: 0; grid-area: sub; justify-self: start; }
  .iSubRow { flex-wrap: wrap; }
  .iTop h1 { font-size: 22px; }
  .iAct { margin-left: 0; }
}
@media (max-width: 560px) {
  .iItem { padding: 8px 12px; }
  .iItem-hd { gap: 6px; row-gap: 4px; min-height: var(--tap); }
  .iWhen { font-size: 10px; }
  .iPend { margin-left: auto; }
  .iAct { width: 100%; margin-left: 0; }
  .iAct .btn { flex: 1; }
  .iItem-bd { margin-top: 0; }
}
</style>
