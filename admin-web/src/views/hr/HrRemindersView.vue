<script setup>
// 管理端「人事提醒」（票 05）：本月该调工龄奖的人 + 历史欠调的 + 档案待补。
//
// 这一页**只提醒，不替人做决定**：工龄奖要人工点「确认调整」才落账，系统绝不自动改
// 档案里的钱。所以两种人行长得不一样 ——
//
//   * `state === 'due'`（现值低于应为，含历史欠调）：给「确认调整到 N 元」，
//     写回去的是**服务端算出的应为值**；
//   * `state === 'over'`（现值高于应为）：只标出来，**不给按钮** —— 一键把 500 改成
//     100 就是自动下调，`docs/adr/0101` 明确不做；要降得去花名册手动填，那才有人工痕迹。
//
// 档位与折算月一律用服务端给的字段（`services/identity/profile.py` 是唯一实现），
// 这一页只负责把它们翻成人话（口径在 `utils/seniorityReminder.js`，有单测钉住）。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import {
  canConfirm,
  confirmPayload,
  currentText,
  gapText,
  isDue,
  monthText,
  reminderCounts,
  stateText,
} from '../../utils/seniorityReminder'

const router = useRouter()
// 与 `pageRoutes.json` 里「花名册」那一行同一条路径（补档案与手改工龄奖都在那儿）。
const ROSTER_PATH = '/workbench/hr/roster'
const REMINDERS_PATH = '/api/hygiene/admin/hr-reminders'

const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
// 改完的一句回执。与 errorText 同一块地方显示，谁最后发生谁说话。
const receipt = ref('')
const busyId = ref(0)
const data = ref(null)

const counts = computed(() => reminderCounts(data.value))
const items = computed(() => data.value?.seniority?.items || [])
const incomplete = computed(() => data.value?.incomplete?.items || [])

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    data.value = await api.get(REMINDERS_PATH)
    state.value = 'ready'
  } catch (err) {
    errorText.value = err.message || '提醒读不出来'
    state.value = 'error'
  }
}

/** 确认调整：把**应为值**写进档案。
 *
 *  失败时先重读、最后再说那句话 —— 顺序反了的话 `load()` 开头的清空会把服务端
 *  的提示抹掉（同待办页 `reportFailure` 的理由）。
 */
async function confirmAdjust(item) {
  if (busyId.value) return
  busyId.value = item.id
  errorText.value = ''
  receipt.value = ''
  try {
    await api.patch(`/api/hygiene/admin/roster/${item.id}`, confirmPayload(item))
    receipt.value = `${item.name || '这位员工'}的工龄奖记成 ${item.should_be} 元`
    await load()
  } catch (err) {
    const message = err.message || '没改成'
    await load()
    errorText.value = message
  } finally {
    if (busyId.value === item.id) busyId.value = 0
  }
}

onMounted(() => {
  // 名字与 `pageRoutes.json` 里 `/workbench/hr/reminders` 那一行同步。
  document.title = workbenchDocumentTitle('人事提醒')
  load()
})
</script>

<template>
  <div class="hygiene-admin hr-reminders">
    <header class="hTop">
      <div>
        <p class="hKicker">人事</p>
        <h1>人事提醒</h1>
        <p class="hSub">
          工龄奖按入职日期算：入职日 ≤ 15 号算当月、≥ 16 号起算下月，满 N 年在「折算月 + N 年」那个月调，
          第 10 年 1000 封顶。这里只提醒，钱点了「确认调整」才落账。
        </p>
      </div>
      <button class="btn" type="button" @click="router.push(ROSTER_PATH)">去花名册</button>
    </header>

    <p v-if="state === 'loading'" class="hHint">正在读提醒…</p>

    <template v-else>
      <p v-if="errorText" class="hErr" role="alert">{{ errorText }}</p>
      <p v-else-if="receipt" class="hOk" role="status">{{ receipt }}</p>

      <section class="hCard">
        <div class="hCard-hd">
          <h2>工龄奖该调</h2>
          <span class="hCount">{{ counts.due }}</span>
          <span v-if="counts.over" class="hFlag">另有 {{ counts.over }} 人调过头</span>
        </div>
        <p v-if="!items.length" class="hEmpty">
          没有要调的：在职的人都对得上档位。
        </p>
        <ul v-else class="hList">
          <li v-for="item in items" :key="item.id" class="hItem">
            <div class="hItem-hd">
              <b>{{ item.name || '（没有名字）' }}</b>
              <span class="hWhen">入职 {{ item.hire_date }}</span>
              <span class="hState" :class="{ over: !isDue(item) }">{{ stateText(item) }}</span>
            </div>
            <p class="hMoney">
              {{ currentText(item) }} → <b>{{ item.should_be }} 元</b>
              <span class="hGap">{{ gapText(item) }}</span>
              <span v-if="monthText(item.next_adjust_month)" class="hNext">
                下次 {{ monthText(item.next_adjust_month) }}
              </span>
              <span v-else class="hNext">已封顶</span>
            </p>
            <p v-if="item.due_month" class="hDue">欠自 {{ monthText(item.due_month) }}</p>
            <div class="hAct">
              <button
                v-if="canConfirm(item)"
                class="btn btn-primary"
                type="button"
                :disabled="busyId !== 0"
                @click="confirmAdjust(item)"
              >确认调整到 {{ item.should_be }} 元</button>
              <p v-else class="hNoAct">
                档案里比应为值高 —— 只标出来，系统不自动下调；要改去花名册手动填。
              </p>
            </div>
          </li>
        </ul>
      </section>

      <section class="hCard">
        <div class="hCard-hd">
          <h2>档案待补</h2>
          <span class="hCount">{{ counts.incomplete }}</span>
        </div>
        <p class="hLead">入职日期空着就算不出工龄奖 —— 他们不在上面的名单里，而是落在这儿。</p>
        <p v-if="!incomplete.length" class="hEmpty">档案都齐。</p>
        <ul v-else class="hPeople">
          <li v-for="person in incomplete" :key="person.id">
            <b>{{ person.name || '（没有名字）' }}</b>
            <span v-if="person.phone" class="hPhone">{{ person.phone }}</span>
            <span class="hMissing">缺入职日期</span>
          </li>
        </ul>
        <button class="btn" type="button" @click="router.push(ROSTER_PATH)">去花名册补档案</button>
      </section>
    </template>
  </div>
</template>

<style scoped>
/* 版式照待办页：深青墨底 + 一张卡一个话题。令牌来自 /hygiene-admin.css（壳一层加载）。 */
.hr-reminders { padding: 18px 16px 40px; max-width: 780px; margin: 0 auto; }
.hTop { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
.hTop h1 { margin: 2px 0 4px; font-size: 20px; color: var(--hy-ink); }
.hKicker { margin: 0; font-size: 11px; letter-spacing: .18em; color: var(--hy-faint); }
.hSub { margin: 0; font-size: 12px; line-height: 1.7; color: var(--hy-muted); }
.hTop .btn { margin-left: auto; flex: none; }
.hHint { margin: 0; font-size: 12.5px; color: var(--hy-muted); }
.hErr {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--hy-radius-md);
  border: 1px solid rgba(255, 138, 128, .35); background: rgba(255, 138, 128, .12);
  font-size: 12.5px; color: var(--hy-coral);
}
.hOk {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--hy-radius-md);
  border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
  font-size: 12.5px; color: var(--hy-mint);
}
.hCard {
  margin-bottom: 14px; padding: 14px; border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-lg); background: var(--hy-surface);
}
.hCard-hd { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.hCard-hd h2 { margin: 0; font-size: 14px; color: var(--hy-ink); }
.hCount {
  min-width: 20px; padding: 1px 7px; border-radius: 999px; text-align: center;
  font-family: var(--font-mono); font-size: 11.5px; color: var(--hy-night); background: var(--hy-amber);
}
.hFlag { margin-left: auto; font-size: 11.5px; color: var(--hy-coral); }
.hLead { margin: 0 0 10px; font-size: 12px; line-height: 1.7; color: var(--hy-muted); }
.hEmpty { margin: 4px 0 0; font-size: 12.5px; color: var(--hy-faint); }
.hList { list-style: none; margin: 0; padding: 0; }
.hItem { padding: 12px 0; border-top: 1px solid var(--hy-line); }
.hItem:first-child { border-top: 0; padding-top: 6px; }
.hItem-hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hItem-hd b { font-size: 14px; color: var(--hy-ink); }
.hWhen { font-family: var(--font-mono); font-size: 12px; color: var(--hy-muted); }
.hState {
  margin-left: auto; padding: 1px 8px; border-radius: 999px; font-size: 11px;
  color: var(--hy-mint); border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
}
.hState.over { color: var(--hy-coral); border-color: rgba(255, 138, 128, .35); background: rgba(255, 138, 128, .12); }
.hMoney { margin: 8px 0 0; font-size: 13px; color: var(--hy-ink); }
.hMoney b { font-family: var(--font-mono); font-size: 14px; }
.hGap { margin-left: 8px; font-size: 12px; color: var(--hy-amber); }
.hNext { margin-left: 8px; font-size: 11.5px; color: var(--hy-faint); }
.hDue { margin: 4px 0 0; font-size: 11.5px; color: var(--hy-muted); }
.hAct { margin-top: 10px; }
.hNoAct { margin: 0; font-size: 11.5px; line-height: 1.7; color: var(--hy-coral); }
.hPeople { list-style: none; margin: 0 0 12px; padding: 0; }
.hPeople li {
  display: flex; align-items: center; gap: 9px; padding: 7px 0;
  border-top: 1px solid var(--hy-line); font-size: 12.5px; color: var(--hy-muted);
}
.hPeople li:first-child { border-top: 0; }
.hPeople b { font-size: 13px; color: var(--hy-ink); }
.hPhone { font-family: var(--font-mono); font-size: 11.5px; }
.hMissing { margin-left: auto; font-size: 11.5px; color: var(--hy-amber); }
</style>
