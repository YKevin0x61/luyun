<script setup>
/**
 * 员工手机端的「整月」页（票 06）。
 *
 * 从「今天」页那张排班卡的「整月」按钮进来（`/workbench/staff/month`）。这一页回答两件事：
 * 这个月我哪几天上班、上什么班；以及**哪天店里人多**（票 13）。
 *
 * 数据两个来源，各管一件事：
 *   · `GET /api/scheduling/me/month`（员工那个 cookie，跟 `/me` 同一扇门，接口上同样
 *     没有 `employee_id` 可填）：格子里是我的班别，外加那天全店上班几个人（`staff_count`）。
 *   · `GET /api/scheduling/me/day?date=…`（票 13，点开某一天才拉）：那天各班是谁、
 *     在哪个区，以及谁休、谁请假。**同事的班次名一个字都不进月历** —— 人数是人数，
 *     我的班是我的班。
 *
 * 三条口径写在页脚里，免得员工自己猜：
 *   · 空格子 = 那天还没排（新装机的本月前半月就是这样：铺班只往今天以后走，不回头补）；
 *   · 「请假」是票 08 起的一态：批过的假跟排班给的「休」分开上色分开放（都是没有班，
 *     但一个是自己提的、店长批的）；
 *   · 右上角的小点 = 那天有自己的申请还没落定（票 06 的那条角标验收，等对方点头 /
 *     等店长批，数据来自 `/me/requests`）。
 *
 * 翻月只在服务端给的展开窗口里走（`stepMonth`）：往后到 `window_end` 所在的月为止，
 * 那之后的格子算「窗口外」，淡出并单独说一句 —— 淡 = 还没铺到，不是「那天没排」。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StaffDaySheet from '../../components/scheduling/StaffDaySheet.vue'
import { useNudgePull } from '../../composables/useNudgePull'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { staffRequest } from '../../utils/hygieneStaff'
import { eachDayInRange } from '../../utils/dateRange'
import { loginRedirectTarget } from '../../utils/loginNext'
import { actFromQuery, dateFromQuery, monthOfDate } from '../../utils/staffDayActions'
import {
  MONTH_HEADS,
  dayBeyondWindow,
  monthCell,
  monthLabel,
  staffCountLabel,
  stepMonth,
} from '../../utils/todayShift'

const router = useRouter()
const route = useRoute()
const state = ref('loading') // loading | ready | error
const employee = ref(null)
const month = ref('') // 'YYYY-MM'；空着就让服务端按本营业月给
const today = ref('')
const windowEnd = ref('')
const lead = ref(0)
const days = ref([])
const errorText = ref('')

const title = computed(() => monthLabel(month.value, today.value))
// 每格只算一次四态：`:class` 与格子里的字共用这份（判据仍在 util 里读一次）。
const cells = computed(() => days.value.map((day) => ({ ...day, ...monthCell(day) })))
// 顶部固定摘要（B 案 v3）：今天在哪、点格子看什么。数据全部来自这条 month 响应。
const todayCell = computed(() => cells.value.find((cell) => cell.is_today) || null)
const todayLine = computed(() => {
  const cell = todayCell.value
  const [, month, day] = String(today.value || '').split('-').map(Number)
  const parts = []
  if (month && day) parts.push(`今天 ${month}/${day}`)
  if (cell) {
    parts.push(cell.text || '还没排')
    if (cell.zone_name) parts.push(cell.zone_name)
  }
  parts.push('点日期看名单')
  return parts.join(' · ')
})
// 窗口之外的日子：排班还没铺到，跟「那天还没排」不是一回事 —— 格子淡出，整月在窗外时另说一句。
const beyond = computed(() => days.value.filter((day) => dayBeyondWindow(day, windowEnd.value)).length)
const outside = computed(() => days.value.length > 0 && beyond.value === days.value.length)
const isThisMonth = computed(() => month.value === today.value.slice(0, 7))
// 往后到展开窗口末就该停：再翻只会看见一个空月（判据在 `stepMonth` 里，页面不自算 90 天）。
const canNext = computed(() => !!stepMonth(month.value, 1, windowEnd.value))

/** 会话没了就回员工登录（`/login` 的员工栏），回来还是这一页。
 *  落点与「已经在登录页上就不再跳」都算在 `loginRedirectTarget` 一处（票 12 收的 O2）。 */
function toLogin() {
  const target = loginRedirectTarget(router.currentRoute.value)
  if (target) router.replace(target)
}

async function load(target, quiet = false) {
  // `quiet`：实时 nudge 触发的重读，不把整页打回 loading。
  if (!quiet) state.value = 'loading'
  try {
    const query = target ? `?month=${encodeURIComponent(target)}` : ''
    const data = await staffRequest(`/api/scheduling/me/month${query}`)
    employee.value = data.employee
    month.value = data.month
    today.value = data.today
    windowEnd.value = data.window_end
    lead.value = data.lead || 0
    days.value = data.days || []
    state.value = 'ready'
  } catch (err) {
    if (err.status === 401) {
      toLogin()
      return
    }
    errorText.value = err.message || '读不到你的班'
    state.value = 'error'
  }
}

/** 翻月：只在拿到的月份上加减，不自己猜今天是几月；到窗口末 `stepMonth` 给 null，就不动。
 *  翻走时把那天那份名单收掉 —— 它属于上一个月的格子，留着只会跟新月份对不上。 */
function step(delta) {
  const next = stepMonth(month.value, delta, windowEnd.value)
  if (!next) return
  closeDay()
  load(next)
}

// ── 点开某一天：那一屏就是「这一天」的操作单（票 15）──────────────────────
//
// 名单、请假、换班三件事都在 `StaffDaySheet` 里（票 13 那份名单是它的上半截）——
// 它是**这一天**唯一的操作面，进去之后不再跳页：日期就是这个格子，换班的人从那天
// 在班的人里点。名单按需拉（一个月 30 天全拉下来没人看），这件事也交给它。
//
// 这一页只记住「点的是哪一天」，而且存**日期字符串**不存那一格对象：月历会重读
// （nudge / 翻月），存对象的话弹层会停在一份旧快照上，而「我这天」那行必须跟着新数据走。
const detailDate = ref('')
const openAct = ref('') // 从今天页带过来的那一屏：'' | 'leave' | 'swap'
const detailDay = computed(() => (
  detailDate.value
    ? cells.value.find((cell) => cell.business_date === detailDate.value) || null
    : null
))

function openDay(cell, act = '') {
  // 窗口外的格子不可点（`:disabled`），这里再挡一次：万一键盘/脚本还能触发，
  // 也不去问服务端要一个注定空白的月外日期。
  if (dayBeyondWindow(cell, windowEnd.value)) return
  detailDate.value = cell.business_date
  openAct.value = act
}

function closeDay() {
  detailDate.value = ''
  openAct.value = ''
}

/** 从「今天」页那两颗按钮跳过来时带的 `?date=&act=`：落到那一天，直接进那一屏。
 *
 *  只在挂载时读一次（`wantedDate` 用完就清）：解析之后 URL 就过期了，留着它反而会在
 *  关掉弹层后又被 `openWanted` 打开一次。坏值由 `dateFromQuery` / `actFromQuery` 挡掉。 */
const wantedDate = ref('')
const wantedAct = ref('')

function openWanted() {
  const date = wantedDate.value
  const act = wantedAct.value
  wantedDate.value = ''
  wantedAct.value = ''
  if (!date) return
  const cell = cells.value.find((item) => item.business_date === date)
  if (cell) openDay(cell, act)
}

// 格子上的两个记号都读同一条 `/me/requests`（一条请求两件事，不分两次拉）：
//   · 右上角的小点  = 那天有**我的**申请还没落定；
//   · 底下那条琥珀  = 那天**有人找我**换班，还没回应。
// 读不出来两个都不标 —— 跟店长月历同一条口径：宁可少一个记号，也别在一个没读到的
// 日子上标错。
const pendingDates = ref(new Set())
const incomingDates = ref(new Set())

async function loadRequestMarks() {
  try {
    const data = await staffRequest('/api/scheduling/me/requests')
    const marks = new Set()
    for (const request of data.requests || []) {
      // 还挂着的那两条才算「申请中」：等对方点头、等店长批。批了 / 驳了 / 撤了都是结果，
      // 格子写的就是结果本身（批过的假写「请假」）。
      if (request.status !== 'pending_peer' && request.status !== 'pending_manager') continue
      const days = eachDayInRange(
        request.start_date,
        request.end_date || request.start_date,
      )
      for (const day of days) marks.add(day)
    }
    pendingDates.value = marks
    // 有人找我换哪几天（票 09 的那几条待回应）：这件事不回应就停在员工这里，所以单独一个
    // 记号，跟「我的申请」（右上角的点）分开 —— 一条在别人手里，一条在我手里。
    const asks = new Set()
    for (const card of data.incoming || []) {
      if (card && card.business_date) asks.add(card.business_date)
    }
    incomingDates.value = asks
  } catch (err) {
    pendingDates.value = new Set()
    incomingDates.value = new Set()
  }
}

function backToThisMonth() {
  if (!isThisMonth.value) load()
}

onMounted(async () => {
  // 名字与清单里 `/workbench/staff/month` 那一行同步。走 `workbenchDocumentTitle` 是为了
  // 那条「· 工作台」后缀：只写裸标题会让它在浏览器标签里跟管理后台的页面分不开。
  document.title = workbenchDocumentTitle('整月')
  // 「今天」页那两颗按钮带着 `?date=&act=` 跳过来（票 15）：先读那一天所在的月份，
  // 再落到那一格 —— 不然跨月时（今天是 10/31、跳过来那天是 11/1 之类）会落在一格空白上。
  wantedDate.value = dateFromQuery(route.query)
  wantedAct.value = actFromQuery(route.query)
  await load(monthOfDate(wantedDate.value) || undefined)
  openWanted()
  // 申请那份自己拉（票 06 的角标）：跟月历并行，读不出来只是不标角标。
  loadRequestMarks()
})

// Esc 关掉那一天那一屏：它由 `StaffDaySheet` 自己监听（弹层是它开的，它自己收），
// 这一页不再重复埋一个 —— 两个监听器同时活着只会让「谁负责关」变得说不清。

// 实时（票 10 收尾）：店长改了某一天、批了假，或者我的申请有了结果 —— 格子上的班别
// 与那两个记号都要跟着变。静默整页重读一次（一格一格改不值得）。
// 正开着的那一天由 `StaffDaySheet` 自己订阅重读（名单 + 申请），这里不再代劳。
useNudgePull({
  id: 'today-month',
  topics: ['scheduling'],
  pull: () => {
    load(month.value, true)
    loadRequestMarks()
  },
})
</script>

<template>
  <div class="month-page hygiene-staff">
    <!-- 页头（B 案 v2）：kicker + H1 + 右侧主动作（回今天）。 -->
    <header class="tTop" :inert="detailDay ? true : null">
      <div class="tTop-main">
        <p class="kicker">我的排班</p>
        <h1 class="tDay">整月</h1>
        <p class="tDate">{{ employee ? employee.name : '' }}</p>
      </div>
      <div class="tTop-side">
        <button class="tBack" type="button" @click="router.push('/workbench/staff/today')">‹ 今天</button>
      </div>
      <!-- 退出**不在这里**（D5）：工作台外壳顶栏那一颗是员工三页共用的唯一一颗
           （`WorkbenchLayout` 的 `WorkbenchExitButton`，员工那一档走 `useStaffLogout`）。 -->
    </header>

    <div class="mBody" :inert="detailDay ? true : null">
      <p v-if="state === 'loading'" class="mSub">正在读这个月的班…</p>

      <template v-else-if="state === 'error'">
        <p class="mSub">{{ errorText }}</p>
        <button class="btn btn-block" type="button" @click="load(month)">重试</button>
        <!-- 坏月份（手改地址栏进来的）光重试只会再错一次：给一条走得掉的路。 -->
        <button v-if="!isThisMonth" class="btn btn-block" type="button" @click="backToThisMonth">
          回到本月
        </button>
      </template>

      <template v-else>
        <!-- 固定摘要（B 案 v3）：一条说清「今天在哪、点格子看什么」。 -->
        <p class="mSummary">{{ todayLine }}</p>

        <!-- 月历面板（B 案 v2）：翻月条 + 周表头 + 钢面方格，三件在一张面板里。 -->
        <section class="mBoard">
          <div class="mNav">
            <button class="mArrow" type="button" aria-label="上个月" @click="step(-1)">‹</button>
            <span class="mLabel">{{ title }}</span>
            <button
              class="mArrow"
              type="button"
              aria-label="下个月"
              :disabled="!canNext"
              @click="step(1)"
            >
              ›
            </button>
          </div>

          <div class="mHead">
            <span v-for="head in MONTH_HEADS" :key="head">{{ head }}</span>
          </div>

          <div class="mGrid">
            <div v-for="n in lead" :key="`lead-${n}`" class="mD mute" />
            <!-- 格子从 div 改成 button（票 13）：点开看那天的名单。窗口外的格子 `:disabled`
                 —— 淡 = 还没铺到，点它只会问出一个注定空白的答案。 -->
            <button
              v-for="cell in cells"
              :key="cell.business_date"
              type="button"
              class="mD"
              :class="[cell.tone, { today: cell.is_today, mute: dayBeyondWindow(cell, windowEnd) }]"
              :disabled="dayBeyondWindow(cell, windowEnd)"
              aria-haspopup="dialog"
              @click="openDay(cell)"
            >
              <span class="n">{{ cell.day }}</span>
              <span class="s">{{ cell.text }}</span>
              <!-- 那天全店上班几个人（票 13）：跟上面那行班别是两个字段 —— 这一行说的是
                   店里，上面那行说的是我。0 与窗口外都不写（`staffCountLabel` 判）。 -->
              <span class="h">{{ staffCountLabel(cell.staff_count) }}</span>
              <!-- 申请中（票 06 那条一直没数据源的验收）：这天有我自己提的、还没落定的
                   请假或换班。跟格子里的班别是两件事，所以做成右上角的小点，不挤那一行字。 -->
              <i v-if="pendingDates.has(cell.business_date)" class="pend" aria-hidden="true" />
              <!-- 有人找我换那天（票 09 的待回应）：另一条线的事（一条在别人手里、一条在我
                   手里），所以是另一个记号。做成格子底下那条琥珀 —— 右上角已经被「我的申请」
                   占了，两个圆点挨在一起谁也认不出谁。 -->
              <i
                v-if="incomingDates.has(cell.business_date)"
                class="ask"
                aria-hidden="true"
              />
            </button>
          </div>
        </section>

        <!-- 图例 LED 行：一格里那几笔各是什么，用 6px 灯 + 小字说清。 -->
        <div class="mLegend">
          <span><i class="dot shift" />上班</span>
          <span><i class="dot rest" />休</span>
          <span><i class="dot half" />休半天</span>
          <span><i class="dot leave" />请假</span>
          <span><i class="dot moved" />班次已调整</span>
          <span><i class="dot none" />还没排</span>
          <span v-if="pendingDates.size"><i class="dot pending" />申请中</span>
          <span v-if="incomingDates.size"><i class="dot asked" />找我换班</span>
        </div>

        <!-- 窗口尽头的两种说法：整月在窗外 vs 只有这个月后半段在窗外（淡掉的那些格子）。 -->
        <p v-if="outside" class="mWarn">
          这个月还没铺到（排班只铺到今天起 90 天，末日 {{ windowEnd }}）：先看前面几个月。
        </p>
        <p v-else-if="beyond" class="mWarn">
          {{ windowEnd }} 之后的格子（淡的那些）还没铺到，不是那天没排。
        </p>
        <button v-if="!isThisMonth" class="btn btn-block" type="button" @click="backToThisMonth">
          回到本月
        </button>

        <!-- 页尾说明（B 案 v3）：四段口径收进折叠入口，常驻只留一行入口。 -->
        <details class="mFoot rules">
          <summary>怎么看这张表 ›</summary>
          <p>
            点开某一天：看那天的名单，也顺手请那天的假、跟那天在班的人换班。<br />
            空着的格子是那天还没排（不是休）—— 休的那天写着「休」。批过的假写「请假」（自己提的、店长批的），跟排班给的「休」不是一回事。右上角的小点 = 那天有你的申请还没落定；格子下面那条琥珀 = 有人找你换那天。<br />
            点开那天能看到完整名单。
          </p>
        </details>
      </template>
    </div>

    <!-- 某一天那一屏（票 13 的名单 + 票 15 的请假与换班）：点格子弹出，员工在这一屏里
         看完那天的事、也把那天的事办掉 —— 日期就是这个格子，不用再选一次。
         B 案 v2 起它是从底部升起的当日面板（只改这一页里的定位，组件本身不动）。 -->
    <StaffDaySheet
      v-if="detailDay"
      :key="detailDate"
      :day="detailDay"
      :employee="employee"
      :today="today"
      :window-end="windowEnd"
      :initial-act="openAct"
      @close="closeDay"
      @changed="loadRequestMarks"
    />
  </div>
</template>

<style scoped>
/* B 案 v2「钢面 · 仪」版式（2026-10-17）：员工手机端 390 优先。
   结构 = 页头（kicker + H1 + 回今天）→ 月历面板（翻月条 + 周表头 + 钢面方格）
   → 图例 LED 行 → 页脚；点格子从底部升起「那一天」的当日面板。
   元素语言只有三种：面板（.mBoard）、方格（.mD）、读数/图例 LED（.dot + 小字）。
   全部走语义令牌；这一页不写裸色值（面板只用 1px 线，不叠投影 —— 设计护栏的
   存量棘轮对本文件是 0 基线，投影由共享壳/弹层那层给）。 */
.month-page {
  min-height: 100vh;
  background: var(--surface-0);
  color: var(--ink);
  font-family: var(--font-sans);
  padding-bottom: 40px;
}

/* ── 页头 ──────────────────────────────────────────────────────────────── */
.tTop {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  /* 手机安全区四项让位（t52，口径同 t49）：顶槽取 max，左右同 max */
  padding: max(18px, var(--safe-t)) max(16px, var(--safe-r)) 14px max(16px, var(--safe-l));
}

.tTop-main { min-width: 0; }

.kicker {
  margin: 0 0 7px;
  font: 500 9.5px/1 var(--font-sans);
  letter-spacing: .26em;
  color: var(--ink-faint);
}

.tDay {
  margin: 0;
  font: 600 26px/1 var(--font-sans);
  letter-spacing: .01em;
  color: var(--ink);
}

.tDate {
  margin: 7px 0 0;
  font-size: 10.5px;
  color: var(--ink-dim);
}

.tTop-side { flex: none; }

/* 回「今天」是一颗按钮：触控 ≥44px、方角（--r-1）。 */
.tBack {
  display: inline-flex;
  align-items: center;
  min-height: var(--tap);
  font: inherit;
  font-size: 11.5px;
  color: var(--ink);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  padding: 0 12px;
  background: transparent;
  cursor: pointer;
}

/* 员工手机上点的主按钮 ≥48px（与「今天」页同一档）；方角。 */
.month-page.hygiene-staff .btn {
  min-height: 48px !important;
  border-radius: var(--r-1);
  background: transparent;
  border: 1px solid var(--line-strong);
  color: var(--ink);
}
.month-page.hygiene-staff .btn:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--action);
  color: var(--action);
}

/* ── 月历面板 ─────────────────────────────────────────────────────────── */
.mBody {
  padding: 0 14px 26px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 固定摘要（B 案 v3）：滚动时贴顶，一直回答「今天在哪」。
   底色取页面底，格子从它下面滚过去。 */
.mSummary {
  position: sticky;
  top: 0;
  z-index: var(--z-shell);
  margin: 0;
  padding: 10px 2px;
  background: var(--surface-0);
  border-bottom: 1px solid var(--line);
  font-family: var(--font-mono), var(--font-sans);
  font-size: 11px;
  color: var(--ink-dim);
}

.mSub {
  margin: 0;
  font-size: 11.5px;
  color: var(--ink-dim);
}

.mBoard {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-2);
  overflow: hidden;
}

.mNav {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}

/* 翻月箭头：控件边界取 --line-strong，触控 ≥44px，方角。 */
.mArrow {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: var(--tap);
  min-height: var(--tap);
  font: inherit;
  font-size: 16px;
  line-height: 1;
  color: var(--ink);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: transparent;
  padding: 0 12px;
  cursor: pointer;
}

.mArrow:disabled {
  opacity: .35;
  cursor: default;
}

.mLabel {
  font-family: var(--font-mono), var(--font-sans);
  font-size: 15px;
  letter-spacing: .02em;
  color: var(--ink);
}

/* 周表头：小字 + 宽字距，底一档次级色。 */
.mHead {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 5px;
  padding: 8px 12px 6px;
  background: var(--surface-2);
  border-bottom: 1px solid var(--line);
}

.mHead span {
  text-align: center;
  font-size: 9.5px;
  letter-spacing: .12em;
  color: var(--ink-faint);
}

.mGrid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 5px;
  padding: 10px 12px 12px;
}

/* 钢面方格：1px 线 + 次级底 + 方角；54px 高（行 ≥44px）。 */
.mD {
  position: relative;
  height: 54px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
  padding: 5px 4px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  /* 票 13 起格子是个 button（点开那天的名单）：把浏览器的按钮外观收掉，
     版式仍由上面这几条说了算。 */
  font: inherit;
  color: inherit;
  appearance: none;
  cursor: pointer;
}

.mD:disabled {
  cursor: default;
}

/* 申请中（票 06 那条角标）：跟格子里那个班别是两件事，所以做成右上角的小点，
   不挤「白班 / 休 / 请假」那一行字。颜色用 action（当前/信息），
   与底下那条琥珀（等我回应的换班）分开。 */
.mD .pend {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--action);
}

.mD .n {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-dim);
  line-height: 1;
}

.mD .s {
  font-family: var(--font-sans);
  font-size: 11.5px;
  line-height: 1.15;
  text-align: center;
}

/* 那天全店上班的人数（票 13）：格子里的第三行，最小的字 —— 它说的是「店里」，
   上面那行班别说的是「我」，所以比班别弱一档，别抢了主行。 */
.mD .h {
  font-family: var(--font-mono), var(--font-sans);
  font-size: 8.5px;
  line-height: 1;
  color: var(--ink-faint);
  white-space: nowrap;
  /* B 案 v3：格子只留「日号 + 班次」两行；「N 人在班」只在点开那天的面板里看
     （面板的名单头会写「N 人在班 · N 人休假」）。留着节点与类名，口径不变。 */
  display: none;
}

.mD.shift .s {
  color: var(--ok);
}

.mD.rest .s {
  color: var(--ink-faint);
}

/* 请假（票 08）：批过的假。跟「休」分开上色 —— 休是排班给的，假是自己提的。 */
.mD.leave .s {
  color: var(--info);
}

/* 半天休（票 07 / ADR 0114）：仍然上班，只是只上半天。自己一档色 ——
   用语义令牌（`--accent-alt`），不新增 `--hy-*` 引用（设计护栏的存量棘轮只减不增）。 */
.mD.half .s {
  color: var(--accent-alt);
  font-size: 10px;
}

.mD.moved .s {
  color: var(--warn);
  font-size: 10px;
}

.mD.none {
  background: transparent;
  border-color: var(--line);
}

.mD.none .n {
  color: var(--ink-faint);
}

/* 月首空格与窗口外的格子都淡掉（跟店长月历的 `.gB-d.mute` 同一个做法）：
   淡 = 这里没有排班可看，不是「那天休」。 */
.mD.mute {
  background: transparent;
  border-color: var(--line);
  opacity: .35;
}

/* 今天那格：action 色边框 + 极浅 action 底（当前/信息那一档，不是发光）。 */
.mD.today {
  border-color: var(--action);
  background: var(--surface-2);
}

/* 今天格用实心标记：日号是一枚实心圆，不再只靠描边。 */
.mD.today .n {
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}

/* ── 图例 LED 行 ──────────────────────────────────────────────────────── */
.mLegend {
  display: flex;
  flex-wrap: nowrap;
  overflow-x: auto;
  align-items: center;
  gap: 4px 8px;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-1);
  font-size: 9px;
  color: var(--ink-faint);
}

.mLegend span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
  white-space: nowrap;
}

/* 图例灯：6px 圆点 + 小字（B 案的状态语言）。 */
.dot {
  width: 5px;
  height: 5px;
  border-radius: var(--r-full);
  display: inline-block;
  background: var(--surface-3);
}

.dot.shift {
  background: var(--ok);
}

.dot.rest {
  background: var(--ink-faint);
}

.dot.leave {
  background: var(--info);
}

/* 半天休（票 07）：跟 `.mD.half` 同一个颜色来源（语义令牌 `--accent-alt`）——
   图例说一套、格子画一套是最阴的一种漂。 */
.dot.half {
  background: var(--accent-alt);
}

.dot.moved {
  background: var(--warn);
}

.dot.none {
  border: 1px solid var(--line-strong);
  background: transparent;
}

/* 申请中：圆点 + action 色（当前/信息），与旁边「班次已调整」的琥珀分开。 */
.dot.pending {
  background: var(--action);
}

.mWarn {
  margin: 0;
  padding: 0 2px;
  font-size: 11px;
  color: var(--warn);
}

.mFoot {
  margin: 0;
  padding: 0 2px;
  font-size: 10.5px;
  color: var(--ink-faint);
  line-height: 1.75;
}

.mFoot > summary {
  display: flex;
  align-items: center;
  min-height: 44px;
  cursor: pointer;
  list-style: none;
  font-size: 11px;
  color: var(--ink-dim);
}

.mFoot > summary::-webkit-details-marker { display: none; }

.mFoot p {
  margin: 0;
  font-size: 10.5px;
  line-height: 1.75;
  color: var(--ink-faint);
}

/* 有人找我换那天（票 09 的待回应）：格子底下一条琥珀短线（待我回应 = --warn）。
   放在底下而不是右上角：右上角是「我的申请」那个小点的地方，两个 6px 的圆点挨在一起
   谁也认不出谁；这条横线也不占格子里那三行字的位置。 */
.mD .ask {
  position: absolute;
  left: 7px;
  right: 7px;
  bottom: 3px;
  height: 2px;
  border-radius: var(--r-full);
  background: var(--warn);
}

/* 图例里那一条：跟格子里的记号同形状（短横线），不画成圆点。 */
.dot.asked {
  width: 11px;
  height: 2px;
  border-radius: var(--r-full);
  background: var(--warn);
}

/* ── 底部当日面板（B 案 v2）：点格子那一天从底部升起，宽度铺满。 ──────
   组件本身（StaffDaySheet）不在这一票的范围里，这里只改它在本页里的定位：
   遮罩贴底、面板铺满、上两角收圆，内容与交互一个字不动。 */
.month-page :deep(.modal-overlay) {
  align-items: flex-end;
  padding: 0;
}

.month-page :deep(.day-sheet) {
  width: 100%;
  max-width: 100%;
  max-height: 86vh;
  border-radius: var(--r-2) var(--r-2) 0 0;
  padding-bottom: calc(16px + var(--safe-b));
}
</style>
