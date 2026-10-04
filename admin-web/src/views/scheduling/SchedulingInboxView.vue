<script setup>
// 店长端「排班 · 待办」（票 08 请假、票 09 换班）：等店长批的申请，
// 加一份「谁还没配规则」的提醒。
//
// 申请是员工在手机上提的（`POST /api/scheduling/me/requests`、`/me/swaps`），审批落在
// 店长手里：这一页就是那张审批台。每条申请**先把结果摊开再让店长按**（票 08 的验收 2）：
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
import { WORKBENCH_HR_HOME } from '../../utils/workbenchCopy'
// 共享样式表由壳加载（`SchedulingLayout.vue`）：三个子页各加载一份会挂出重复的
// <link>，壳一层管住就跟卫生管理端一个做法。
import {
  approveReceipt,
  kindText,
  noteText,
  previewLine,
  requestRangeText,
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
 *  等于把「这条申请已经处理过了」这句话当场抹掉 —— 店长点完「批准」什么都看不到
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
  // `request_not_pending` 挡得住数据被改坏，挡不住店长看到「点了没反应 / 回执对不上」。
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
    // 别让店长对着一条已经不存在的申请反复点 —— 服务端那句话留到最后说。
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

onMounted(() => {
  document.title = '排班待办'
  load()
})

// 实时（票 10 收尾）：员工提了新的申请、撤回了、或者对方回了话 —— 待办列表重拉一次。
// 这一页本来就是「等别人动作」的地方，没有实时就只能靠人反复刷新。
useNudgePull({ id: 'workbench-inbox', topics: ['scheduling'], pull: load })
</script>

<template>
  <div class="hygiene-admin inbox-page">
    <header class="iTop">
      <div>
        <p class="iKicker">排班</p>
        <h1>待办</h1>
        <p class="iSub">
          <template v-if="today">今天 {{ today }} · </template>请假与换班都在这里批；批完那天就记成请假 / 对调，月历上带青点。
        </p>
      </div>
      <button class="btn" type="button" @click="router.push(WORKBENCH_HR_HOME)">回到月历</button>
    </header>

    <p v-if="state === 'loading'" class="iHint">正在读待办…</p>

    <template v-else>
      <p v-if="errorText" class="iErr" role="alert">{{ errorText }}</p>
      <p v-else-if="receipt" class="iOk" role="status">{{ receipt }}</p>

      <section class="iCard">
        <div class="iCard-hd">
          <h2>申请等着批</h2>
          <span class="iCount">{{ requests.length }}</span>
        </div>
        <p v-if="!requests.length" class="iEmpty">
          没有等着批的申请。员工在手机上提了请假会出现在这里；换班要对方先点同意，才会轮到这儿。
        </p>
        <ul v-else class="iList">
          <li v-for="request in requests" :key="request.id" class="iItem">
            <div class="iItem-hd">
              <b>{{ request.employee_name || '（没有名字）' }}</b>
              <span v-if="request.kind === 'swap'" class="iPeer">⇄ {{ request.peer_name || '（对方）' }}</span>
              <span class="iKind">{{ kindText(request.kind) }}</span>
              <span class="iWhen">{{ requestRangeText(request.start_date, request.end_date) }}</span>
              <span class="iDays">{{ (request.days || []).length }} 天</span>
              <span class="iPend">等你批</span>
            </div>
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
            <div class="iAct">
              <button
                class="btn btn-primary"
                type="button"
                :disabled="busyId !== 0"
                @click="decide(request, 'approve')"
              >批准</button>
              <button
                class="btn btn-danger"
                type="button"
                :disabled="busyId !== 0"
                @click="rejectTarget = request"
              >驳回</button>
            </div>
          </li>
        </ul>
      </section>

      <section class="iCard">
        <div class="iCard-hd">
          <h2>还没配规则的人</h2>
          <span class="iCount">{{ ruleless.active.length }}</span>
        </div>
        <p class="iLead">
          没配规则就不会被排班，请假提了也没班可改。名单上停用或没批准的人不在这儿提醒。
        </p>
        <p v-if="!ruleless.active.length" class="iEmpty">在职的人都配好了。</p>
        <ul v-else class="iPeople">
          <li v-for="person in ruleless.active" :key="person.id">
            <b>{{ person.name }}</b>
            <span>{{ person.job_title || '员工' }}</span>
            <span v-if="person.phone" class="iPhone">{{ person.phone }}</span>
          </li>
        </ul>
        <p v-if="ruleless.muted.length" class="iMuted">
          另有 {{ ruleless.muted.length }} 个人已停用或还没批准，不提醒。
        </p>
        <button class="btn" type="button" @click="router.push(WORKBENCH_HR_HOME)">去配固定班</button>
      </section>
    </template>

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
/* 版式照票 07 的月历页：深青墨底 + 一张卡一个话题。令牌来自 /hygiene-admin.css；
   `.btn` 那一族也在那儿。 */
.inbox-page { padding: 18px 16px 40px; max-width: 780px; margin: 0 auto; }
.iTop { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
.iTop h1 { margin: 2px 0 4px; font-size: 20px; color: var(--hy-ink); }
.iKicker { margin: 0; font-size: 11px; letter-spacing: .18em; color: var(--hy-faint); }
.iSub { margin: 0; font-size: 12px; line-height: 1.7; color: var(--hy-muted); }
.iTop .btn { margin-left: auto; flex: none; }
.iHint { margin: 0; font-size: 12.5px; color: var(--hy-muted); }
.iErr {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--hy-radius-md);
  border: 1px solid rgba(255, 138, 128, .35); background: rgba(255, 138, 128, .12);
  font-size: 12.5px; color: var(--hy-coral);
}
.iOk {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--hy-radius-md);
  border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
  font-size: 12.5px; color: var(--hy-mint);
}
.iCard {
  margin-bottom: 14px; padding: 14px; border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-lg); background: var(--hy-surface);
}
.iCard-hd { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.iCard-hd h2 { margin: 0; font-size: 14px; color: var(--hy-ink); }
.iCount {
  min-width: 20px; padding: 1px 7px; border-radius: 999px; text-align: center;
  font-family: var(--font-mono); font-size: 11.5px; color: var(--hy-night); background: var(--hy-amber);
}
.iLead { margin: 0 0 10px; font-size: 12px; line-height: 1.7; color: var(--hy-muted); }
.iEmpty { margin: 4px 0 0; font-size: 12.5px; color: var(--hy-faint); }
.iMuted { margin: 10px 0; font-size: 11.5px; color: var(--hy-faint); }
.iList { list-style: none; margin: 0; padding: 0; }
.iItem { padding: 12px 0; border-top: 1px solid var(--hy-line); }
.iItem:first-child { border-top: 0; padding-top: 6px; }
.iItem-hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.iItem-hd b { font-size: 14px; color: var(--hy-ink); }
.iKind {
  padding: 1px 8px; border-radius: 999px; font-size: 11px;
  color: var(--hy-aqua); border: 1px solid var(--hy-aqua); background: rgba(94, 234, 212, .10);
}
.iWhen { font-family: var(--font-mono); font-size: 12.5px; color: var(--hy-ink); }
/* 换班卡上的「⇄ 对方」：这件事是两个人，姓名并排写才看得懂谁跟谁换。 */
.iPeer { font-size: 13px; color: var(--hy-amber); }
.iDays { font-size: 11.5px; color: var(--hy-muted); }
.iPend { margin-left: auto; font-size: 11.5px; color: var(--hy-amber); }
.iNote { margin: 8px 0 0; font-size: 12.5px; line-height: 1.7; color: var(--hy-muted); }
.iPrev { list-style: none; margin: 8px 0 0; padding: 0; }
.iPrev li {
  font-size: 12px; line-height: 1.9; color: var(--hy-ink);
  padding-left: 10px; border-left: 2px solid var(--hy-mint-line);
}
.iPrev li.past { color: var(--hy-faint); border-left-color: var(--hy-line-strong); }
.iPrev li.none { color: var(--hy-muted); border-left-color: var(--hy-line-strong); }
.iAct { display: flex; gap: 8px; margin-top: 10px; }
.iPeople { list-style: none; margin: 0 0 12px; padding: 0; }
.iPeople li {
  display: flex; align-items: center; gap: 9px; padding: 7px 0;
  border-top: 1px solid var(--hy-line); font-size: 12.5px; color: var(--hy-muted);
}
.iPeople li:first-child { border-top: 0; }
.iPeople b { font-size: 13px; color: var(--hy-ink); }
.iPhone { margin-left: auto; font-family: var(--font-mono); font-size: 11.5px; }
</style>
