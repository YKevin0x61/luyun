<script setup>
/**
 * 人事端的「考勤表」页（考勤表导出票 01 / 假期台账票 03）：**当月、一人一张卡**。
 *
 * 卡片上是这个人本月每一天的「上班 / 休 / 待补」：排了班 = 上班、有排班行但班次为空
 * = 休、**压根没有排班行** = 待补（轮转规则没铺到，要人工确认那几天）。**今天之后的
 * 日子留空** —— 那是「还没到」，不是「待补」。
 *
 * 卡片上另外那几列（职位 / 底薪 / 年限 / 是否新入职 / 当月净时长）都由服务端从花名册与
 * 加班台账里取，这一层只摆出来，一个数都不自己算。
 *
 * 票 03 往卡片上加的是**假期台账**那一块：实际出勤 / 月例休 / 法定节假日补休 / 生日假 /
 * 年假 / 上月存假，与由它们相加得到的可用天数池、本月出勤天数、存假；展开能看算式。
 * 当月**只算不落库**，所以顶部明写「暂算值（未封账）」—— 这一页的数还会随排班与加班变。
 * 还没批的加班 / 补钟只**标色**并给出待批小时数，一分都不进净时长（故事 14）。
 *
 * 这一页仍然是**只读**的，而且只算当月：没有月份选择器、没有导出按钮、也没有写请求。
 * 逐日改格（票 06）、手填列（票 05）、封账（票 04）与导出（票 08）都往这一页上加，
 * 但它们各有自己的票 —— 在那之前，这一页上多一颗按钮就是多一条没人测的路径。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../../api/client'
import {
  dayStateText,
  formatDays,
  formatNetHours,
  leaveFormulaText,
  newHireText,
  pendingOvertimeText,
} from '../../utils/attendanceSheet'
import { WORKBENCH_HR_HOME, workbenchDocumentTitle } from '../../utils/workbenchCopy'

const router = useRouter()

const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
const sheet = ref(null)

/** 表头那一份月框（哪一天是今天 / 还没到）按营业日索引，卡片上的日子从它取号与标记 ——
 *  页面不自己从 1 数到 30（那样 2 月与 31 天的月份会各错一次）。 */
const frame = computed(
  () => new Map((sheet.value?.days || []).map((day) => [day.business_date, day])),
)

const employees = computed(() =>
  (sheet.value?.employees || []).map((person) => ({
    ...person,
    cells: (person.days || []).map((day) => ({
      ...day,
      ...(frame.value.get(day.business_date) || {}),
    })),
  })),
)

/** 这个人这个月有几天是「待补」—— 规则没铺到的那几天要一眼看得出来（验收 3 的用法）。 */
function todoDays(person) {
  return (person.cells || []).filter((cell) => cell.state === 'todo').length
}

/** 卡片上假期台账那几项的摆法（标签 + 服务端算好的值）：顺序与 `spec.md` 的公式一致，
 *  展开的算式也按这个顺序念 —— 店长对着卡片能一格一格核。 */
function leaveStats(person) {
  const leave = person.leave || {}
  return [
    { label: '实际出勤', value: formatDays(leave.actual_days) },
    { label: '月例休', value: formatDays(leave.monthly_rest) },
    { label: '法定节假日补休', value: formatDays(leave.holiday_makeup) },
    { label: '生日假', value: formatDays(leave.birthday_leave) },
    { label: '年假', value: formatDays(leave.annual_leave) },
    { label: '上月存假', value: formatDays(leave.previous_balance) },
    { label: '可用天数池', value: formatDays(leave.pool) },
    { label: '本月出勤天数', value: formatDays(leave.month_attendance_days) },
    { label: '存假', value: formatDays(leave.balance) },
  ]
}

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    sheet.value = await api.get('/api/attendance/sheet')
    state.value = 'ready'
  } catch (err) {
    errorText.value = err.message || '读不出这个月的考勤表'
    state.value = 'error'
  }
}

onMounted(() => {
  document.title = workbenchDocumentTitle('考勤表')
  load()
})
</script>

<template>
  <div class="hygiene-admin attendance-page">
    <header class="aTop">
      <div>
        <p class="aKicker">人事</p>
        <h1>考勤表</h1>
        <p class="aSub">
          <template v-if="sheet">当月 {{ sheet.month }} · 今天 {{ sheet.today }} · </template>
          一人一张卡，打开就是当月：今天之后的日子留空，没铺到排班的那天写「待补」。
        </p>
      </div>
      <button class="btn" type="button" @click="router.push(WORKBENCH_HR_HOME)">
        回到月历
      </button>
    </header>

    <p v-if="state === 'loading'" class="aHint">正在读这个月的考勤表…</p>
    <p v-else-if="errorText" class="aErr" role="alert">{{ errorText }}</p>

    <template v-else>
      <!-- 当月只算不落库（票 03 / ADR 0113）：这一条要看得见，否则半个月的数会被拿去结账。 -->
      <p v-if="sheet && sheet.provisional" class="aProvisional" role="status">
        暂算值（未封账）：这个月的数还会随排班与加班变，封账之后才定稿。
      </p>
      <p v-if="!employees.length" class="aEmpty">
        这个月还没有在职员工，也没有人有排班或加班记录。
      </p>
      <article v-for="person in employees" :key="person.id" class="aCard">
        <header class="aCard-hd">
          <b class="aName">{{ person.name || '（没有名字）' }}</b>
          <span v-if="todoDays(person)" class="aTodo">待补 {{ todoDays(person) }} 天</span>
        </header>
        <p class="aStats">
          <span class="aStat"><i>职位</i><b>{{ person.job_title || '—' }}</b></span>
          <span class="aStat">
            <i>底薪</i><b>{{ person.base_salary === null || person.base_salary === undefined ? '—' : `${person.base_salary} 元` }}</b>
          </span>
          <span class="aStat">
            <i>年限</i><b>{{ person.seniority_bonus === null || person.seniority_bonus === undefined ? '—' : `${person.seniority_bonus} 元` }}</b>
          </span>
          <!-- 入职日期为空的人这一格是**空的**（服务端给 `null`）：留空，不写成「否」——
               「算不出来」与「不是新来的」是两件事。 -->
          <span class="aStat"><i>是否新入职</i><b>{{ newHireText(person.new_hire) }}</b></span>
          <!-- 净时长只算**已批准**的单：还没批的那几笔在这儿标色并报出小时数，
               一分都不进这个数字（故事 14）。 -->
          <span class="aStat" :class="{ 'is-pending': person.pending_count > 0 }">
            <i>当月净时长</i><b class="aNet">{{ formatNetHours(person.net_hours) }}</b>
            <em v-if="person.pending_count" class="aPending">
              {{ pendingOvertimeText(person.pending_count, person.pending_hours) }}
            </em>
          </span>
        </p>
        <div class="aDays">
          <span
            v-for="cell in person.cells"
            :key="cell.business_date"
            class="aDay"
            :class="[`is-${cell.state || 'future'}`, { 'is-today': cell.is_today }]"
            :title="cell.business_date"
          >
            <i class="aDayNum">{{ cell.day }}</i>
            <b class="aDayText">{{ dayStateText(cell.state) }}</b>
          </span>
        </div>
        <!-- 假期台账（票 03）：每张卡把自己的账摊开；算式收在「看算式」后面，
             摊开后能一格一格核（服务端下发的数，页面一个都不自己算）。 -->
        <div v-if="person.leave" class="aLeave">
          <p class="aLvRow">
            <span v-for="stat in leaveStats(person)" :key="stat.label" class="aLv">
              <i>{{ stat.label }}</i><b>{{ stat.value }}</b>
            </span>
          </p>
          <details class="aFormula">
            <summary>看算式</summary>
            <p class="aFormulaText">{{ leaveFormulaText(person.leave) }}</p>
          </details>
        </div>
      </article>
    </template>
  </div>
</template>

<style scoped>
/* 版式照人事组的其它页：深青墨底、一张卡一个人。令牌**只用语义层**（`--ink` / `--surface-1`
   / `--line` / `--ok` / `--warn` / `--r-*` / `--fs-*`，定义在 `theme.workbench.css`）——
   新文件在设计护栏里按 strict 处理（`__tests__/tokenDebt.json` 的棘轮只认存量，
   `src/styles/__tests__/designGuard.test.js` 盯着），写兼容层的 `--hy-*` 或不透明色值会红。 */
.attendance-page { padding: 18px 16px 40px; max-width: 860px; margin: 0 auto; }
.aTop { display: flex; align-items: flex-start; gap: var(--sp-3); margin-bottom: var(--sp-4); }
.aTop h1 { margin: 2px 0 var(--sp-1); font-size: var(--fs-20); color: var(--ink); }
.aKicker { margin: 0; font-size: var(--fs-11); letter-spacing: var(--ls-4); color: var(--ink-faint); }
.aSub { margin: 0; font-size: var(--fs-12); line-height: 1.7; color: var(--ink-dim); }
.aTop .btn { margin-left: auto; flex: none; }
.aHint, .aEmpty { margin: 0; font-size: var(--fs-13); color: var(--ink-dim); }
/* 当月是暂算值（票 03）：这一条不是装饰 —— 它是「别拿这个数去结账」的告示。 */
.aProvisional {
  margin: 0 0 var(--sp-3); padding: 9px var(--sp-3); border-radius: var(--r-2);
  border: 1px solid var(--info); background: var(--info-soft);
  font-size: var(--fs-12); color: var(--ink);
}
.aErr {
  margin: 0 0 var(--sp-3); padding: 9px var(--sp-3); border-radius: var(--r-2);
  border: 1px solid var(--danger); background: var(--danger-soft);
  font-size: var(--fs-13); color: var(--danger-bright);
}
.aCard {
  margin-bottom: var(--sp-4); padding: var(--sp-4); border: 1px solid var(--line);
  border-radius: var(--r-3); background: var(--surface-1);
}
.aCard-hd { display: flex; align-items: center; gap: var(--sp-2); margin-bottom: var(--sp-2); }
.aName { font-size: var(--fs-14); color: var(--ink); }
.aTodo {
  padding: 1px var(--sp-2); border-radius: var(--r-full); font-size: var(--fs-11);
  color: var(--warn); border: 1px solid var(--warn); background: transparent;
}
.aStats { display: flex; flex-wrap: wrap; gap: 6px var(--sp-4); margin: 0 0 var(--sp-3); }
.aStat { display: inline-flex; align-items: baseline; gap: 5px; font-size: var(--fs-12); }
.aStat i { font-style: normal; color: var(--ink-faint); }
.aStat b { font-size: var(--fs-13); color: var(--ink); }
.aNet { font-family: var(--font-mono); }
.aDays {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(38px, 1fr)); gap: var(--sp-1);
}
.aDay {
  display: flex; flex-direction: column; align-items: center; gap: 1px;
  padding: var(--sp-1) 2px; border-radius: var(--r-1);
  border: 1px solid var(--line); background: transparent;
}
.aDayNum { font-family: var(--font-mono); font-size: var(--fs-11); font-style: normal; color: var(--ink-faint); }
.aDayText { font-size: var(--fs-11); font-weight: 500; color: var(--ink-dim); }
/* 三态各自的色：上班是常规（薄荷）、休淡下去、待补最醒目（琥珀，要人工确认）。 */
.aDay.is-work { border-color: var(--line-strong); background: var(--ok-soft); }
.aDay.is-work .aDayText { color: var(--ok); }
.aDay.is-rest { background: transparent; }
.aDay.is-rest .aDayText { color: var(--ink-faint); }
.aDay.is-todo { border-color: var(--warn); background: transparent; }
.aDay.is-todo .aDayText { color: var(--warn); }
/* 今天之后：**留空**，连边框都不给 —— 一眼看得出那一片还没发生。 */
.aDay.is-future { border-color: transparent; }
.aDay.is-today { outline: 1px solid var(--warn); outline-offset: 1px; }
/* 未审批的加班 / 补钟（票 03 / 故事 14）：**只标色**，数不进净时长。 */
.aStat.is-pending .aNet { color: var(--warn); }
.aPending {
  padding: 0 var(--sp-2); border-radius: var(--r-full); border: 1px solid var(--warn);
  font-style: normal; font-size: var(--fs-11); color: var(--warn);
}
/* 假期台账那一块（票 03）：九项摊在卡片上，算式收在「看算式」后面。 */
.aLeave { margin-top: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
.aLvRow { display: flex; flex-wrap: wrap; gap: 5px var(--sp-4); margin: 0 0 var(--sp-2); }
.aLv { display: inline-flex; align-items: baseline; gap: 5px; font-size: var(--fs-12); }
.aLv i { font-style: normal; color: var(--ink-faint); }
.aLv b { font-family: var(--font-mono); font-size: var(--fs-13); color: var(--ink); }
.aFormula summary { cursor: pointer; font-size: var(--fs-12); color: var(--ink-dim); }
.aFormulaText {
  margin: var(--sp-2) 0 0; font-family: var(--font-mono); font-size: var(--fs-11);
  line-height: 1.9; color: var(--ink-dim);
}
</style>
