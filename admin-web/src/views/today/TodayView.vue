<script setup>
/**
 * 员工手机端的「今天」页（票 05）。
 *
 * 原型：`.scratch/scheduling/prototype/today-variants.html` 的 A · 两块 —— 用户选的
 * 「登录后一个入口，上下两块，各挂各的系统牌子」。这一票只有上面那块（排班给的
 * 「今天上不上班」）：下面那块卫生待办卡下一张票接，这会儿留一条去老入口的路，
 * 不然员工登录后只剩这一张卡，卫生的活就没地方看了。
 *
 * 数据只有一个来源：`GET /api/scheduling/me`（员工那个 cookie，接口上没有
 * `employee_id` 可填）。整屏没有钟点 —— 班次没有起止时刻，钟点只在卫生那边。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { staffRequest } from '../../utils/hygieneStaff'
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
  // 整月有地方可去了（票 06）；请假、换班等第 8、9 张票。
  { key: 'month', label: '整月', to: '/today/month' },
]

function open(entry) {
  if (entry.to) {
    router.push(entry.to)
    return
  }
  note.value = `「${entry.label}」还没开放`
}

async function load() {
  state.value = 'loading'
  try {
    const data = await staffRequest('/api/scheduling/me')
    employee.value = data.employee
    days.value = data.days || []
    state.value = 'ready'
  } catch (err) {
    if (err.status === 401) {
      // 会话没了（换了手机、店里停了账号、cookie 过期）：回员工登录，回来还是这一页。
      // 跟卫生首页同一个走法（`leaveForStaffLogin`）：把当前地址整个带过去，
      // 员工只有一套登录，登录页也是同一个。
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

onMounted(() => {
  document.title = '今天'
  load()
})
</script>

<template>
  <div class="today-page hygiene-staff">
    <header class="tTop">
      <span class="tDay">今天</span>
      <span class="tDate">{{ today ? dayLabel(today.business_date) : '' }}</span>
      <span v-if="employee" class="tMe">{{ employee.name }}</span>
    </header>

    <div class="tA-body">
      <p v-if="state === 'loading'" class="tA-sub">正在读你的班…</p>

      <template v-else-if="state === 'error'">
        <p class="tA-sub">{{ errorText }}</p>
        <button class="btn btn-block" type="button" @click="load">重试</button>
      </template>

      <template v-else>
        <section class="tA-card sched">
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

        <!-- 卫生那块（原型 A 的下半张卡）下一张票接上。 -->
        <section class="tA-card hyg">
          <div class="tA-hd">
            <span class="tag hyg">卫生</span>
            <em>下一张票接上</em>
          </div>
          <p class="tA-sub">卫生待办卡还没接上，先用老入口。</p>
          <div class="acts">
            <button class="btn" type="button" @click="router.push('/hygiene')">
              去卫生待办 ›
            </button>
          </div>
        </section>

        <p class="tA-foot">
          排班只说班次，不说几点上班 —— 钟点只有卫生那边才有（逾期点）。
        </p>
      </template>
    </div>
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
  margin-left: auto;
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

.tA-foot {
  margin: 0;
  padding: 2px 4px;
  font-size: 10.5px;
  color: var(--hy-faint);
  line-height: 1.75;
}
</style>
