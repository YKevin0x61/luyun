<script setup>
// 管理端「人事提醒」（票 05 建页，票 06 加生日那一块）：本月该调工龄奖的人 + 历史欠调的
// + 本月生日 + 档案待补。
//
// 这一页**只提醒，不替人做决定**：工龄奖要人工点「确认调整」才落账，系统绝不自动改
// 档案里的钱。所以两种人行长得不一样 ——
//
//   * `state === 'due'`（现值低于应为，含历史欠调）：给「确认调整到 N 元」，
//     写回去的是**服务端算出的应为值**；
//   * `state === 'over'`（现值高于应为）：只标出来，**不给按钮** —— 一键把 500 改成
//     100 就是自动下调，`docs/adr/0101` 明确不做；要降得去花名册手动填，那才有人工痕迹。
//
// 档位、折算月、生日、今天/已过/未到一律用服务端给的字段（`services/identity/profile.py`
// 是唯一实现），这一页只负责把它们翻成人话（口径在两个 util 里，各有单测钉住）。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import {
  birthdayStateText,
  birthdayText,
  leapNoteText,
} from '../../utils/birthdayReminder'
import {
  canConfirm,
  confirmPayload,
  currentText,
  gapText,
  isDue,
  missingText,
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
const birthdays = computed(() => data.value?.birthdays?.items || [])
const incomplete = computed(() => data.value?.incomplete?.items || [])
// 健康证到期（2026-10-08 从首页那一格并进来）：临期与已过期的人，判据在服务端。
const certs = computed(() => data.value?.certs?.items || [])

/** 到期还剩几天 / 过期几天。`days_left` 由服务端按同一套阈值算好，这里只翻文案。
 *
 *  未办理的人（`state: 'none'`）`days_left` 是 `null`，**必须先判它** ——
 *  `Number(null)` 是 0，直接往下算会把他们写成「今天到期」。 */
function certDaysText(person) {
  if (person?.days_left === null || person?.days_left === undefined) return ''
  const days = Number(person.days_left)
  if (!Number.isFinite(days)) return ''
  if (days < 0) return `已过期 ${Math.abs(days)} 天`
  if (days === 0) return '今天到期'
  return `还剩 ${days} 天`
}

/** 健康证那一栏的三种状态（2026-10-13 起含「未办理」）：none / soon / expired。 */
function certStateText(person) {
  if (person?.state === 'none') return '未办理'
  return person?.state === 'expired' ? '已过期' : '临期'
}

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
      <p class="hKicker">人事 · 档案</p>
      <div class="hTitleRow">
        <h1>人事提醒</h1>
        <button class="btn hAction" type="button" @click="router.push(ROSTER_PATH)">去花名册</button>
      </div>
      <p class="hSub">
        工龄奖按入职日期算：入职日 ≤ 15 号算当月、≥ 16 号起算下月，满 N 年在「折算月 + N 年」那个月调，
        第 10 年 1000 封顶。这里只提醒，钱点了「确认调整」才落账。
      </p>
    </header>

    <p v-if="state === 'loading'" class="hHint">正在读提醒…</p>

    <template v-else>
      <p v-if="errorText" class="hErr" role="alert">{{ errorText }}</p>
      <p v-else-if="receipt" class="hOk" role="status">{{ receipt }}</p>

      <!-- 四块 2×2 等宽面板：工龄奖 / 生日 / 健康证 / 档案待补。数字只出现在面板头。 -->
      <div class="hBoard">
        <section class="hCard">
          <div class="hCard-hd">
            <i class="hLed warn"></i>
            <h2>工龄奖该调</h2>
            <span class="hHint">{{ counts.due }} 人</span>
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
                {{ item.current == null ? '未调' : item.current }} → <b>{{ item.should_be }}</b>
                <span class="hGap">({{ isDue(item) ? '+' : '−' }}{{ Math.abs(Number(item.gap) || 0) }})</span>
              </p>
              <p class="hDue">{{ item.due_month || '—' }}</p>
              <p class="hNext">{{ item.next_adjust_month || '封顶' }}</p>
              <div class="hAct">
                <button
                  v-if="canConfirm(item)"
                  class="btn btn-primary"
                  type="button"
                  :disabled="busyId !== 0"
                  @click="confirmAdjust(item)"
                >确认调整到 {{ item.should_be }} 元</button>
                <p v-else class="hNoAct">不自动下调 · 去花名册改</p>
              </div>
            </li>
          </ul>
        </section>

        <!-- 生日（票 06）：生日取自身份证第 7–14 位，只列在职的人。已过 / 今天 / 未到
             由服务端判，这里只上色 —— 「就是今天」那一行最该被一眼看见。 -->
        <section class="hCard">
          <div class="hCard-hd">
            <i class="hLed sig"></i>
            <h2>本月生日</h2>
            <span class="hHint">{{ birthdays.length }} 人</span>
          </div>
          <p class="hLead">生日以身份证上的日期为准；平年的 2 月 29 日按 2 月 28 日提醒。</p>
          <p v-if="!birthdays.length" class="hEmpty">这个月没有在职的人过生日。</p>
          <ul v-else class="hPeople hBirthdays">
            <li v-for="person in birthdays" :key="person.id">
              <b>{{ person.name || '（没有名字）' }}</b>
              <span class="hState" :class="person.state">{{ birthdayStateText(person) }}</span>
              <span class="hMeta">
                <span class="hWhen">{{ birthdayText(person.on) }}</span>
                <span v-if="leapNoteText(person)" class="hNext">{{ leapNoteText(person) }}</span>
              </span>
            </li>
          </ul>
        </section>

        <!-- 健康证（2026-10-08 从首页那一格并进来；2026-10-13 加入「未办理」）：未办理 /
             临期 / 已过期三种人。阈值在服务端（`profile.health_cert_status`：办理日 +
             12 个月，到期前 30 天算临期），这里只上色。**未办理的人排在最前面**。 -->
        <section class="hCard">
          <div class="hCard-hd">
            <i class="hLed bad"></i>
            <h2>健康证</h2>
            <span class="hHint">{{ certs.length }} 人</span>
          </div>
          <p class="hLead">
            还没办证的人排在最前面，接下去才是临期与已过期的（按办理日期起满一年算，到期前 30 天算临期）。
          </p>
          <p v-if="!certs.length" class="hEmpty">在职的人健康证都齐、也都没到期。</p>
          <ul v-else class="hPeople hCerts">
            <li v-for="person in certs" :key="person.id">
              <b>{{ person.name || '（没有名字）' }}</b>
              <span class="hState" :class="{ over: person.state !== 'ok' }">
                {{ certStateText(person) }}
              </span>
              <span class="hMeta">
                <span class="hPhone">{{ person.phone }}</span>
                <span class="hWhen">{{ person.expires_on || '—' }}</span>
                <span class="hNext">{{ certDaysText(person) }}</span>
              </span>
            </li>
          </ul>
        </section>

        <section class="hCard">
          <div class="hCard-hd">
            <i class="hLed warn"></i>
            <h2>档案待补</h2>
            <span class="hHint">{{ counts.incomplete }} 人</span>
          </div>
          <p class="hLead">
            入职日期空着算不出工龄奖，身份证空着算不出生日，离职日期空着对不上账 —— 他们不在上面的名单里，而是落在这儿。
          </p>
          <p v-if="!incomplete.length" class="hEmpty">档案都齐。</p>
          <ul v-else class="hPeople">
            <li v-for="person in incomplete" :key="person.id">
              <b>{{ person.name || '（没有名字）' }}</b>
              <span v-if="person.phone" class="hPhone">{{ person.phone }}</span>
              <span class="hMissing">{{ missingText(person.missing) }}</span>
            </li>
          </ul>
          <div class="hFoot">
            <button class="btn" type="button" @click="router.push(ROSTER_PATH)">去花名册补档案</button>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* B 案 v3（2026-10-17 版式审查）：1052 容器；页头 3 行（kicker / H1 + 去花名册同基线 /
   副标题说明）；四块 2×2 等宽面板（工龄奖 / 生日 / 健康证 / 档案待补），数字只出现在
   面板头。工龄奖一行五列：姓名 | 300→400(+100) | 欠自 | 下次 | 按钮。 */
.hr-reminders {
  display: flex; flex-direction: column; gap: var(--sp-4); width: 100%;
  padding: 18px clamp(16px, 4vw, 32px) calc(40px + var(--safe-b));
  max-width: 1052px; margin: 0 auto; font-family: var(--font-sans);
}
/* 页头 3 行网格：kicker / H1 + 主操作同基线 / 副标题。 */
.hTop {
  display: grid; grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "kick kick" "title action" "sub sub";
  align-items: end; column-gap: var(--sp-4); row-gap: 7px;
}
.hKicker {
  grid-area: kick; margin: 0; font-family: var(--font-sans); font-size: 9.5px; line-height: 1;
  font-weight: 600; letter-spacing: .26em; color: var(--ink-faint);
}
.hTitleRow { grid-area: title; display: flex; align-items: baseline; gap: var(--sp-4); min-width: 0; }
.hTop h1 {
  margin: 0; font-family: var(--font-sans); font-size: 26px; line-height: 1;
  font-weight: 600; letter-spacing: .01em; color: var(--ink);
}
.hAction { margin-left: auto; flex: none; border-radius: var(--r-1); min-height: var(--tap); }
.hSub { grid-area: sub; margin: 0; max-width: 760px; font-size: var(--fs-12); line-height: 1.6; color: var(--ink-dim); }
.hLed { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; }
.hLed.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.hLed.bad { background: var(--danger); box-shadow: 0 0 0 3px var(--wb-danger-a16); }
.hLed.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }
.hHint { margin: 0; font-size: 12.5px; color: var(--ink-dim); }
.hErr {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--wb-danger-a45); background: var(--danger-soft);
  font-size: 12.5px; color: var(--danger-bright);
}
.hOk {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--line-strong); background: var(--ok-soft);
  font-size: 12.5px; color: var(--ok);
}
/* 四块 2×2 等宽面板（每块 510 左右），两栏 stretch 同高。 */
.hBoard { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; align-items: stretch; }
/* 面板（唯一卡片语言）。 */
.hCard {
  display: flex; flex-direction: column; min-width: 0;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: var(--hy-shadow-sm); overflow: hidden;
}
.hCard-hd { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.hCard-hd h2 {
  margin: 0; font-family: var(--font-sans); font-size: 12.5px; line-height: 1;
  font-weight: 600; letter-spacing: .06em; color: var(--ink);
}
.hCard-hd .hHint {
  margin-left: auto; font-family: var(--font-mono); font-size: 9.5px; line-height: 1;
  color: var(--ink-faint);
}
.hFlag { font-size: 9.5px; color: var(--danger-bright); }
.hLead { margin: 0; padding: 11px 14px 0; font-size: 11px; line-height: 1.5; color: var(--ink-dim); }
.hEmpty { margin: 0; padding: 14px; font-size: 12px; color: var(--ink-faint); }
.hList { list-style: none; margin: 0; padding: 0; }
/* 一条工龄奖 = 一行人：姓名 | 300→400(+100) | 欠自 | 下次 | 按钮。 */
.hItem {
  display: grid;
  grid-template-columns: minmax(84px, .95fr) minmax(90px, 1fr) minmax(66px, .7fr) minmax(62px, .65fr) auto;
  align-items: center; gap: 6px 8px; min-height: 56px; padding: 8px 12px;
  border-top: 1px solid var(--wb-ink-a08);
}
.hItem:first-child { border-top: 0; }
.hItem:hover { background: var(--surface-2); }
.hItem-hd { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; min-width: 0; }
.hItem-hd b { font-size: 11.5px; font-weight: 600; color: var(--ink); }
.hWhen { font-family: var(--font-mono); font-size: 8px; color: var(--ink-dim); white-space: nowrap; }
.hMoney { margin: 0; font-family: var(--font-mono); font-size: 9px; color: var(--ink-dim); white-space: nowrap; }
.hMoney b { font-size: 10.5px; font-weight: 600; color: var(--ink); }
.hGap { font-size: 8.5px; color: var(--warn); white-space: nowrap; }
.hDue, .hNext { margin: 0; font-family: var(--font-mono); font-size: 8.5px; color: var(--ink-dim); white-space: nowrap; }
.hAct { justify-self: end; }
.hAct .btn { padding: 0 8px; border-radius: var(--r-1); min-height: var(--tap); font-size: 10px; white-space: nowrap; }
.hNoAct { margin: 0; max-width: 110px; font-size: 8.5px; line-height: 1.3; color: var(--danger-bright); text-align: right; }
/* 状态 = 6px LED + 9px 小字；逾期 / 超差的文字用 --danger-bright。 */
.hState {
  display: inline-flex; align-items: center; gap: 5px; white-space: nowrap;
  font-size: 9px; letter-spacing: .06em; color: var(--warn);
}
.hState::before { content: ""; width: 6px; height: 6px; flex: none; border-radius: 50%; background: currentColor; }
.hState.over { color: var(--danger-bright); }
.hState.over::before { background: var(--danger); }
.hState.upcoming { color: var(--action); }
.hState.today { color: var(--ok); font-weight: 600; }
.hState.past { color: var(--ink-faint); }
/* 侧栏名单：名字 / 状态 / 副行（日期、电话、说明）。 */
.hPeople { list-style: none; margin: 0; padding: 0 14px; }
.hPeople li {
  display: grid; grid-template-columns: minmax(0, 1fr) auto;
  align-items: center; gap: 2px 10px; min-height: 44px; padding: 8px 0;
  border-top: 1px solid var(--wb-ink-a08); font-size: 11px; color: var(--ink-dim);
}
.hPeople li:first-child { border-top: 0; }
.hPeople b { min-width: 0; font-size: 12px; font-weight: 600; color: var(--ink); }
.hPeople .hState { justify-self: end; }
.hMeta {
  grid-column: 1 / -1; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  font-family: var(--font-mono); font-size: 9.5px; color: var(--ink-faint);
}
.hPhone { font-family: var(--font-mono); font-size: 9.5px; color: var(--ink-faint); }
.hMissing { justify-self: end; font-size: 9.5px; color: var(--warn); }
.hFoot { margin-top: auto; padding: 10px 14px; border-top: 1px solid var(--wb-ink-a08); }
.hFoot .btn { width: 100%; border-radius: var(--r-1); min-height: var(--tap); }
.hCard > .hFoot { margin-top: auto; }
@media (max-width: 900px) {
  .hBoard { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .hTop { grid-template-columns: 1fr; grid-template-areas: "kick" "title" "sub"; }
  .hTop h1 { font-size: 22px; }
  .hItem { grid-template-columns: minmax(0, 1fr) auto; gap: 4px 10px; }
  .hItem-hd { grid-column: 1; }
  .hMoney { grid-column: 1; }
  .hDue, .hNext { grid-column: 1; }
  .hAct { grid-column: 2; grid-row: 1 / span 3; align-self: center; }
  .hNoAct { text-align: left; }
}
</style>
