<script setup>
/**
 * 员工手机端的「整月」页（票 06）。
 *
 * 从「今天」页那张排班卡的「整月」按钮进来（`/workbench/me/month`）。这一页回答两件事：
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
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useNudgePull } from '../../composables/useNudgePull'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { staffRequest } from '../../utils/hygieneStaff'
import { eachDayInRange } from '../../utils/dateRange'
import { loginRedirectTarget } from '../../utils/loginNext'
import {
  MONTH_HEADS,
  dayBeyondWindow,
  dayLabel,
  isMe,
  monthCell,
  monthLabel,
  myDayText,
  offLabel,
  rosterSummary,
  staffCountLabel,
  stepMonth,
} from '../../utils/todayShift'

useScopedStylesheet('/hygiene-admin.css')

const router = useRouter()
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

// ── 点开某一天看全店名单（票 13）─────────────────────────────────────────
//
// 名单是**按需拉的**：一个月 30 天，把每天的全店名单一次全拉下来（30 × 20 人）没人看，
// 点开哪天才拉哪天。`detailDay` 是那一格（格子上已经有我的班别与当天人数，标题照着写）。
const detailDay = ref(null)
const detail = ref(null)
const detailState = ref('idle') // idle | loading | ready | error
const detailError = ref('')

const detailGroups = computed(() => (detail.value ? detail.value.groups : []))
const detailOff = computed(() => (detail.value ? detail.value.off_people : []))
const detailSummary = computed(() =>
  detail.value ? rosterSummary(detail.value.total, detail.value.off_count) : '',
)
// 「我这天」那一行从**月历那份数据**里按日期取（不是打开弹层那一刻的快照）：店长改了班、
// nudge 让月历重读之后，这一行跟着变，弹层里那份名单也由 `loadDay` 静默重拉（见下面的 pull）。
const detailMine = computed(() => {
  const key = detailDay.value && detailDay.value.business_date
  return key ? cells.value.find((cell) => cell.business_date === key) || null : null
})
// 四态判据（`monthCell`）复用，只在上班那态后面接我自己的区名 —— 工作区不进格子
// （票 06 口径 1），点开这一屏就是它该出现的地方。
const mineText = computed(() => (detailMine.value ? myDayText(detailMine.value) : ''))

async function loadDay(cell, quiet = false) {
  if (!quiet) {
    detail.value = null
    detailState.value = 'loading'
  }
  try {
    const data = await staffRequest(
      `/api/scheduling/me/day?date=${encodeURIComponent(cell.business_date)}`,
    )
    // 连点两格时晚到的那份不能盖掉新的那份：按日期认人，不按「最后回来的就是对的」。
    if (detailDay.value?.business_date !== cell.business_date) return
    detail.value = data
    detailState.value = 'ready'
  } catch (err) {
    if (err.status === 401) {
      toLogin()
      return
    }
    if (detailDay.value?.business_date !== cell.business_date) return
    // 静默重拉失败（网络抖一下）不该把已经读出来的名单换成一句错误：留着旧的，下次再说。
    if (quiet) return
    detailError.value = err.message || '读不到这天的名单'
    detailState.value = 'error'
  }
}

function openDay(cell) {
  // 窗口外的格子不可点（`:disabled`），这里再挡一次：万一键盘/脚本还能触发，
  // 也不去问服务端要一个注定空白的月外日期。
  if (dayBeyondWindow(cell, windowEnd.value)) return
  detailDay.value = cell
  loadDay(cell)
}

function closeDay() {
  detailDay.value = null
  detail.value = null
  detailState.value = 'idle'
}

function onKeydown(event) {
  if (event.key === 'Escape' && detailDay.value) closeDay()
}

// 自己提的、**还没落定**的申请覆盖了哪几天（票 06 那条一直没数据源的角标验收）。
//
// 员工视角的「待处理」= 还在等对方点头、或等店长批的那几条；批了 / 驳了 / 撤了都不算
// （那些已经是结果，格子上写的就是结果本身）。读不出来就不标 —— 跟店长月历同一条口径：
// 宁可少一个记号，也别在一个没读到的日子上标错。
const pendingDates = ref(new Set())
async function loadPendingMarks() {
  try {
    const data = await staffRequest('/api/scheduling/me/requests')
    const marks = new Set()
    for (const request of data.requests || []) {
      if (request.status !== 'pending_peer' && request.status !== 'pending_manager') continue
      const days = eachDayInRange(
        request.start_date,
        request.end_date || request.start_date,
      )
      for (const day of days) marks.add(day)
    }
    pendingDates.value = marks
  } catch (err) {
    pendingDates.value = new Set()
  }
}

function backToThisMonth() {
  if (!isThisMonth.value) load()
}

onMounted(() => {
  // 名字与清单里 `/workbench/me/month` 那一行同步。走 `workbenchDocumentTitle` 是为了
  // 那条「· 工作台」后缀：只写裸标题会让它在浏览器标签里跟管理后台的页面分不开。
  document.title = workbenchDocumentTitle('整月')
  load()
  // 申请那份自己拉（票 06 的角标）：跟月历并行，读不出来只是不标角标。
  loadPendingMarks()
  // Esc 关掉那天的名单（跟「今天」页那几个弹层同一个走法）。
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => document.removeEventListener('keydown', onKeydown))

// 实时（票 10 收尾）：店长改了某一天、批了假，或者我的申请有了结果 —— 格子上的班别
// 与那个小点都要跟着变。静默整页重读一次（一格一格改不值得）。正开着的那天名单也静默重拉：
// 别人被临时调走时，「谁在班」这一屏不该停在旧数上（票 13）。
useNudgePull({
  id: 'today-month',
  topics: ['scheduling'],
  pull: () => {
    load(month.value, true)
    loadPendingMarks()
    if (detailDay.value) loadDay(detailDay.value, true)
  },
})
</script>

<template>
  <div class="month-page hygiene-staff">
    <header class="tTop" :inert="detailDay ? true : null">
      <span class="tDay">整月</span>
      <span class="tDate">{{ employee ? employee.name : '' }}</span>
      <button class="tBack" type="button" @click="router.push('/workbench/me/today')">‹ 今天</button>
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
          </button>
        </div>

        <div class="mLegend">
          <span><i class="dot shift" />上班</span>
          <span><i class="dot rest" />休</span>
          <span><i class="dot leave" />请假</span>
          <span><i class="dot moved" />班次已调整</span>
          <span><i class="dot none" />还没排</span>
          <span v-if="pendingDates.size"><i class="dot pending" />申请中</span>
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

        <p class="mFoot">
          格子里的小字是那天全店上班的人数，点开某天看当天名单。<br />
          空着的格子是那天还没排（不是休）—— 休的那天写着「休」。<br />
          批过的假写「请假」（自己提的、店长批的），跟排班给的「休」不是一回事。<br />
          右上角的小点 = 那天有你的申请还没落定（等对方点头 / 等店长批）；钟点只有卫生那边才有。
        </p>
      </template>
    </div>

    <!-- 某一天的名单（票 13）：点格子弹出，员工看的是「今天店里谁在、在哪、谁不来」。
         容器走主题里那套 `.modal-overlay/.modal-box`（跟本页请假、加班那几处同一个走法）。 -->
    <div
      v-if="detailDay"
      class="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="day-roster-title"
      @click.self="closeDay"
    >
      <div class="modal-box day-sheet">
        <div class="modal-header">
          <h3 id="day-roster-title">{{ dayLabel(detailDay.business_date) }}</h3>
          <button class="btn" type="button" @click="closeDay">关闭</button>
        </div>

        <p v-if="detailState === 'loading'" class="mSub">读名单…</p>

        <template v-else-if="detailState === 'error'">
          <p class="mSub">{{ detailError }}</p>
          <button class="btn btn-block" type="button" @click="openDay(detailDay)">重试</button>
        </template>

        <template v-else-if="detail">
          <!-- 我这天：格子不写工作区（票 06 口径 1），这一屏是它该出现的地方。 -->
          <p class="rMine">我这天：<b>{{ mineText }}</b></p>
          <p class="rSum">{{ detailSummary }}</p>

          <div v-for="group in detailGroups" :key="group.shift_name" class="rGroup">
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
              </li>
            </ul>
          </div>
          <p v-if="!detailGroups.length" class="mSub">没人排班</p>

          <div v-if="detailOff.length" class="rGroup">
            <div class="rHead">
              <span class="rName">休假</span>
              <span class="rCount">{{ detailOff.length }} 人</span>
            </div>
            <ul class="rList">
              <li
                v-for="person in detailOff"
                :key="person.id"
                :class="{ me: isMe(person, employee && employee.id) }"
              >
                <span class="rWho">{{ person.name }}</span>
                <!-- 休与请假分开写（票 08 的口径）：判据在 `offLabel` 一处。 -->
                <span class="rWhere" :class="{ leave: person.leave }">{{ offLabel(person) }}</span>
              </li>
            </ul>
          </div>
          <p v-else class="mSub">没人休假</p>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 一屏装完整月（验收 5）：七列 + 六行，格子 54px，手机上不横向滚动。
   令牌与 .btn 的底座来自 public/hygiene-admin.css（员工端作用域 .hygiene-staff）。 */
.month-page {
  min-height: 100vh;
  background:
    radial-gradient(130% 46% at 50% -6%, rgba(63, 224, 176, .1), transparent 62%),
    var(--hy-bg);
  padding-bottom: 40px;
}

.tTop {
  display: flex;
  align-items: baseline;
  gap: 10px;
  /* 手机安全区四项让位（t52，口径同 t49）：顶槽取 max，左右同 max */
  padding: max(18px, var(--safe-t)) max(16px, var(--safe-r)) 14px max(16px, var(--safe-l));
}

.tDay {
  font-family: var(--font-song);
  font-size: 20px;
  letter-spacing: .16em;
  font-weight: 600;
}

.tDate {
  font-size: 11.5px;
  color: var(--hy-muted);
}

.tBack {
  margin-left: auto;
  font: inherit;
  font-size: 11.5px;
  color: var(--hy-muted);
  border: 1px solid var(--hy-line);
  border-radius: 999px;
  padding: 4px 11px;
  background: var(--hy-surface-2);
  cursor: pointer;
}

.mBody {
  padding: 0 14px 26px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mSub {
  margin: 0;
  font-size: 11.5px;
  color: var(--hy-muted);
}

.mNav {
  display: flex;
  align-items: center;
  gap: 12px;
}

.mArrow {
  font: inherit;
  font-size: 16px;
  line-height: 1;
  color: var(--hy-muted);
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-sm);
  background: var(--hy-surface-2);
  padding: 7px 13px;
  cursor: pointer;
}

.mArrow:disabled {
  opacity: .35;
  cursor: default;
}

.mLabel {
  font-family: var(--font-song);
  font-size: 16px;
  letter-spacing: .12em;
}

.mHead {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 4px;
  padding: 0 0 5px;
}

.mHead span {
  text-align: center;
  font-size: 10px;
  color: var(--hy-faint);
}

.mGrid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 4px;
}

.mD {
  position: relative;
  height: 54px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--hy-surface-2);
  padding: 6px 4px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
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
   不挤「白班 / 休 / 请假」那一行字。颜色用珊瑚：琥珀这一页已经被「已调整」占了
   （图例里那两个色块挨着看，同色不同义最容易看错）。 */
.mD .pend {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--hy-coral);
}

.mD .n {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--hy-muted);
  line-height: 1;
}

.mD .s {
  font-family: var(--font-song);
  font-size: 12.5px;
  letter-spacing: .06em;
  line-height: 1.15;
  text-align: center;
}

/* 那天全店上班的人数（票 13）：格子里的第三行，最小的字 —— 它说的是「店里」，
   上面那行班别说的是「我」，所以比班别弱一档，别抢了主行。 */
.mD .h {
  font-family: var(--font-mono);
  font-size: 9px;
  line-height: 1;
  color: var(--hy-faint);
  white-space: nowrap;
}

.mD.shift .s {
  color: var(--hy-jade);
}

.mD.rest .s {
  color: var(--hy-faint);
}

/* 请假（票 08）：批过的假。跟「休」分开上色 —— 休是排班给的，假是自己提的。 */
.mD.leave .s {
  color: var(--hy-aqua);
}

.mD.moved .s {
  color: var(--hy-amber);
  font-size: 10.5px;
}

.mD.none {
  background: transparent;
  border-color: var(--hy-line);
}

.mD.none .n {
  color: var(--hy-faint);
}

/* 月首空格与窗口外的格子都淡掉（跟店长月历的 `.gB-d.mute` 同一个做法）：
   淡 = 这里没有排班可看，不是「那天休」。 */
.mD.mute {
  background: transparent;
  border-color: var(--hy-line);
  opacity: .35;
}

.mD.today {
  border-color: var(--hy-mint-line);
  background: var(--hy-mint-soft);
}

.mD.today .n {
  color: var(--hy-jade);
  font-weight: 700;
}

.mLegend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 10.5px;
  color: var(--hy-faint);
}

.mLegend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 2px;
  display: inline-block;
  background: var(--hy-surface-3);
}

.dot.shift {
  background: var(--hy-jade);
}

.dot.rest {
  background: var(--hy-faint);
}

.dot.leave {
  background: var(--hy-aqua);
}

.dot.moved {
  background: var(--hy-amber);
}

.dot.none {
  border: 1px solid var(--hy-line);
  background: transparent;
}

/* 申请中：圆点 + 珊瑚色，跟旁边「班次已调整」的琥珀方块区分开（两件事，别同色）。 */
.dot.pending {
  border-radius: 50%;
  background: var(--hy-coral);
}

.mWarn {
  margin: 0;
  font-size: 11px;
  color: var(--hy-amber);
}

.mFoot {
  margin: 0;
  padding: 2px 4px;
  font-size: 10.5px;
  color: var(--hy-faint);
  line-height: 1.75;
}

/* ── 某一天的名单（票 13）────────────────────────────────────────────────
   容器是主题里的 `.modal-overlay/.modal-box`（布局与遮罩都在那儿），这里只管里面
   这份名单的版式与员工端的令牌。 */
.day-sheet {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.day-sheet .modal-header {
  margin-bottom: 0;
}

/* 我这天：这一屏唯一说「我」的地方，所以给它自己的分量（格子那三行都是紧凑的）。 */
.rMine {
  margin: 0;
  font-size: 12.5px;
  color: var(--hy-muted);
}

.rMine b {
  font-family: var(--font-song);
  font-size: 14px;
  letter-spacing: .06em;
  color: var(--hy-jade);
}

.rSum {
  margin: 0;
  font-size: 11px;
  color: var(--hy-faint);
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
  border-bottom: 1px solid var(--hy-line);
}

.rHead .rName {
  font-family: var(--font-song);
  font-size: 12.5px;
  letter-spacing: .08em;
}

.rHead .rCount {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--hy-faint);
}

.rList {
  list-style: none;
  margin: 0;
  padding: 0;
}

.rList li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 5px 2px;
  font-size: 12.5px;
  border-bottom: 1px dashed var(--hy-line);
}

.rList li:last-child {
  border-bottom: 0;
}

/* 我在名单里的那一行：薄荷底 + 名字后面一枚「我」—— 按 id 认（`isMe`），不按名字。 */
.rList li.me {
  background: var(--hy-mint-soft);
  border-radius: var(--hy-radius-sm);
  padding-left: 6px;
  padding-right: 6px;
}

.rList li.me .rWho::after {
  content: '我';
  margin-left: 6px;
  font-size: 9.5px;
  color: var(--hy-jade);
  border: 1px solid var(--hy-mint-line);
  border-radius: 999px;
  padding: 0 4px;
}

.rWhere {
  font-size: 11.5px;
  color: var(--hy-muted);
}

/* 请假用青色（跟月历格子里那一态同色），休仍是灰的 —— 两件事两个颜色。 */
.rWhere.leave {
  color: var(--hy-aqua);
}
</style>
