<script setup>
/**
 * 员工手机端的「整月」页（票 06）。
 *
 * 从「今天」页那张排班卡的「整月」按钮进来（`/today/month`）。这一页只回答一件事：
 * 这个月我哪几天上班、上什么班。格子里是班别，不是钟点（班次本来就没有起止时刻）。
 *
 * 数据只有一个来源：`GET /api/scheduling/me/month`（员工那个 cookie，跟 `/me` 同一扇门，
 * 接口上同样没有 `employee_id` 可填 —— 只看得到自己的班）。
 *
 * 两条口径写在页脚里，免得员工自己猜：
 *   · 空格子 = 那天还没排（新装机的本月前半月就是这样：铺班只往今天以后走，不回头补）；
 *   · 「请假」是票 08 起的一态：批过的假跟排班给的「休」分开上色分开放（都是没有班，
 *     但一个是自己提的、店长批的）；换班的角标还在第 9 张票里。
 *
 * 翻月只在服务端给的展开窗口里走（`stepMonth`）：往后到 `window_end` 所在的月为止，
 * 那之后的格子算「窗口外」，淡出并单独说一句 —— 淡 = 还没铺到，不是「那天没排」。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { staffRequest } from '../../utils/hygieneStaff'
import {
  MONTH_HEADS,
  dayBeyondWindow,
  monthCell,
  monthLabel,
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

async function load(target) {
  state.value = 'loading'
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
      // 会话没了：回员工登录，回来还是这一页（跟「今天」页同一个走法）。
      router.replace({
        path: '/hygiene/login',
        query: { next: router.currentRoute.value.fullPath },
      })
      return
    }
    errorText.value = err.message || '读不到你的班'
    state.value = 'error'
  }
}

/** 翻月：只在拿到的月份上加减，不自己猜今天是几月；到窗口末 `stepMonth` 给 null，就不动。 */
function step(delta) {
  const next = stepMonth(month.value, delta, windowEnd.value)
  if (next) load(next)
}

function backToThisMonth() {
  if (!isThisMonth.value) load()
}

onMounted(() => {
  document.title = '整月'
  load()
})
</script>

<template>
  <div class="month-page hygiene-staff">
    <header class="tTop">
      <span class="tDay">整月</span>
      <span class="tDate">{{ employee ? employee.name : '' }}</span>
      <button class="tBack" type="button" @click="router.push('/today')">‹ 今天</button>
    </header>

    <div class="mBody">
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
          <div
            v-for="cell in cells"
            :key="cell.business_date"
            class="mD"
            :class="[cell.tone, { today: cell.is_today, mute: dayBeyondWindow(cell, windowEnd) }]"
          >
            <span class="n">{{ cell.day }}</span>
            <span class="s">{{ cell.text }}</span>
          </div>
        </div>

        <div class="mLegend">
          <span><i class="dot shift" />上班</span>
          <span><i class="dot rest" />休</span>
          <span><i class="dot leave" />请假</span>
          <span><i class="dot moved" />班次已调整</span>
          <span><i class="dot none" />还没排</span>
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
          只看得到你自己的班。<br />
          空着的格子是那天还没排（不是休）—— 休的那天写着「休」。<br />
          批过的假写「请假」（自己提的、店长批的），跟排班给的「休」不是一回事。<br />
          换班的角标等那张票做好再加；钟点只有卫生那边才有。
        </p>
      </template>
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
  padding: 18px 16px 14px;
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
  height: 54px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--hy-surface-2);
  padding: 6px 4px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
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
</style>
