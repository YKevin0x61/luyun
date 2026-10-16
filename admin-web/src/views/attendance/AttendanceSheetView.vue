<script setup>
/**
 * 人事端的「考勤表」页（票 01 + 票 06）：**当月、一人一张卡**，点某一天就地改。
 *
 * 卡片上是这个人本月每一天的「上班 / 休 / 待补」：排了班 = 上班、有排班行但班次为空
 * = 休、**压根没有排班行** = 待补（轮转规则没铺到，要人工确认那几天）。**今天之后的
 * 日子留空** —— 那是「还没到」，不是「待补」，也不给点（`spec.md` 验收 45）。
 *
 * 卡片上另外那几列（职位 / 底薪 / 年限 / 是否新入职 / 当月净时长）都由服务端从花名册与
 * 加班台账里取，这一层只摆出来，一个数都不自己算。
 *
 * 票 06：可编辑的那一格点开是一个下拉（现在挂的班次 / 别的启用中班次 / 休），选完打
 * `PUT /api/attendance/sheet/{员工}/{营业日}`，服务端复用排班的**单日覆盖** —— 只动那一天、
 * 不写轮转规则、不动下个月。拒绝的理由（那天有未决的换班 / 请假、超出可补记窗口、
 * 班次已停用）由服务端给中文，这一层原样显示在那一格的下面。
 *
 * 封账（票 04）、手填列（票 05）与导出（票 08）都往这一页上加，但它们各有自己的票。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../../api/client'
import { dayStateText, formatNetHours, newHireText } from '../../utils/attendanceSheet'
import { WORKBENCH_HR_HOME, workbenchDocumentTitle } from '../../utils/workbenchCopy'

const router = useRouter()

const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
const sheet = ref(null)

/** 正在改的那一格（`营业日` 或 null）与正在等回执的那一格。 */
const editingDate = ref('')
const savingDate = ref('')
/** 那一格上要显示的话（服务端拒绝的理由）。 */
const dayError = ref(null) // { business_date, text } | null

/** 表头那一份月框（哪一天是今天 / 还没到）按营业日索引，卡片上的日子从它取号与标记 ——
 *  页面不自己从 1 数到 30（那样 2 月与 31 天的月份会各错一次）。 */
const frame = computed(
  () => new Map((sheet.value?.days || []).map((day) => [day.business_date, day])),
)

/** 卡片上的日子：把服务端那份月框（今天 / 还没到）并到每人每天的格子上去。
 *
 *  这里刻意**不写成 `computed`**：改完一格会重拉一次整张表，而 `computed` 在
 *  「重挂 / 重新渲染」那一拍上会拿着旧的那一份（实测：页面上的班次名停在改之前，
 *  而 `sheet` 已经是新的了）。一次渲染算一遍这点开销，远不值得拿一个会骗人的缓存换。
 */
function employees() {
  return (sheet.value?.employees || []).map((person) => ({
    ...person,
    cells: (person.days || []).map((day) => ({
      ...day,
      ...(frame.value.get(day.business_date) || {}),
    })),
  }))
}

/** 这个人这个月有几天是「待补」—— 规则没铺到的那几天要一眼看得出来（验收 3 的用法）。 */
function todoDays(person) {
  return (person.cells || []).filter((cell) => cell.state === 'todo').length
}

/** 班次名：`shift_id` 去 `shifts` 里找；班次被停用 / 硬删掉时给空串（不编一个名字）。 */
function shiftName(person, shiftId) {
  if (shiftId === null || shiftId === undefined) return ''
  const shift = (person.shifts || []).find((item) => item.id === shiftId)
  return shift ? shift.name : ''
}

/** 那一格上写什么：休 / 上班（跟班次）/ 待补；今天之后留空。 */
function cellLabel(person, cell) {
  if (cell.state === 'work') return shiftName(person, cell.shift_id) || dayStateText(cell.state)
  return dayStateText(cell.state)
}

/** 这一格能不能点：今天之后留空、没到的那天不给改。 */
function editable(cell) {
  return Boolean(cell.state) && !cell.is_future
}

function openEditor(cell) {
  if (!editable(cell) || savingDate.value) return
  dayError.value = null
  editingDate.value = editingDate.value === cell.business_date ? '' : cell.business_date
}

function errorOn(businessDate) {
  return dayError.value && dayError.value.business_date === businessDate
    ? dayError.value.text
    : ''
}

/** 下拉里选了什么 → 写请求的 body。`rest` 是「那天休」，`todo` 没进下拉（没有「删掉那天」这个动作）。 */
function bodyFor(value) {
  return value === 'rest' ? { is_rest: true } : { shift_id: Number(value) }
}

/** 选完就写：那天立刻按服务端的样子重画（不猜一个本地状态自己改）。 */
async function pick(person, cell, event) {
  const value = event.target.value
  editingDate.value = ''
  savingDate.value = cell.business_date
  dayError.value = null
  try {
    await api.put(
      `/api/attendance/sheet/${person.id}/${cell.business_date}`,
      bodyFor(value),
    )
    // nudge 不带数据，这一页也不等它：自己重拉一次，那天（与「待补」计数）立刻变。
    // **这次重拉不走 `load()`**：那条路会把 `state` 打回 `loading`（整页闪一下），
    // 而改一格只该让那一格动。失败时**不动**页面上已经渲染的那一份 ——
    // 显示一个没落库的班次比报错更糟。
    sheet.value = await api.get('/api/attendance/sheet')
  } catch (err) {
    dayError.value = { business_date: cell.business_date, text: err.message || '这天没改成' }
  } finally {
    savingDate.value = ''
  }
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
          点一个已经过去的日子就能改它 —— 只改那一天，不动轮转规则。
        </p>
      </div>
      <button class="btn" type="button" @click="router.push(WORKBENCH_HR_HOME)">
        回到月历
      </button>
    </header>

    <p v-if="state === 'loading'" class="aHint">正在读这个月的考勤表…</p>
    <p v-if="state === 'error' && errorText" class="aErr" role="alert">{{ errorText }}</p>

    <!-- 卡片这一棵**不进 `v-if/v-else`**（用 `v-show` 藏）：它是改完一格要立刻重画的那
         一棵，而重挂一棵 `v-else` 子树会让里面的格子停在旧值上（实测：整页重新挂载的
         那一拍，格子还是改之前的班次名）。 -->
    <div v-show="state === 'ready'" class="aSheet">
      <p v-if="!employees().length" class="aEmpty">
        这个月还没有在职员工，也没有人有排班或加班记录。
      </p>
      <article v-for="person in employees()" :key="person.id" class="aCard">
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
          <span class="aStat">
            <i>当月净时长</i><b class="aNet">{{ formatNetHours(person.net_hours) }}</b>
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
            <!-- 还没到的日子是一块**死的**格子（不渲染任何控件）：验收 45 说的是
                 「不能编辑」，能点一下再弹一句拒绝不算数。 -->
            <select
              v-if="editingDate === cell.business_date"
              class="aDayPick"
              :aria-label="`改 ${person.name} ${cell.business_date} 的班`"
              :value="cell.shift_id === null || cell.shift_id === undefined ? 'rest' : String(cell.shift_id)"
              @change="pick(person, cell, $event)"
            >
              <option v-for="shift in person.shifts" :key="shift.id" :value="String(shift.id)">
                {{ shift.name }}
              </option>
              <option value="rest">休</option>
            </select>
            <button
              v-else-if="editable(cell)"
              class="aDayBtn"
              type="button"
              :disabled="Boolean(savingDate)"
              @click="openEditor(cell)"
            >
              <i class="aDayNum">{{ cell.day }}</i>
              <b class="aDayText">{{ cellLabel(person, cell) }}</b>
            </button>
            <template v-else>
              <i class="aDayNum">{{ cell.day }}</i>
              <b class="aDayText">{{ cellLabel(person, cell) }}</b>
            </template>
            <small v-if="errorOn(cell.business_date)" class="aDayErr" role="alert">
              {{ errorOn(cell.business_date) }}
            </small>
          </span>
        </div>
      </article>
    </div>
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
  /* 改不了的那一格（今天之后）不该出现可点的光标。 */
  cursor: default;
}
.aDayNum { font-family: var(--font-mono); font-size: var(--fs-11); font-style: normal; color: var(--ink-faint); }
.aDayText { font-size: var(--fs-11); font-weight: 500; color: var(--ink-dim); }
/* 能点的那一格是一颗按钮，但长得跟别的格子一样：卡片是 30 个格子，多一层按钮边框会吵。 */
.aDayBtn {
  display: flex; flex-direction: column; align-items: center; gap: 1px; width: 100%;
  padding: 0; border: 0; background: transparent; color: inherit;
  font: inherit; cursor: pointer;
}
.aDayBtn:focus-visible { outline: 1px solid var(--accent); outline-offset: 1px; }
.aDay:has(.aDayBtn:hover) { border-color: var(--line-strong); background: var(--ok-soft); }
/* 就地编辑：下拉就摆在这一格里（比弹层少一次「我在改谁」的确认）。 */
.aDayPick {
  width: 100%; max-width: 100%; padding: 0; border: 0; background: transparent;
  color: var(--ink); font-size: var(--fs-11); text-align: center; text-align-last: center;
}
/* 服务端拒绝的理由写在**那一格**下面（不是页面顶端）：店长点的就是这一格。 */
.aDayErr { font-size: var(--fs-11); line-height: 1.4; color: var(--danger-bright); text-align: center; }
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
</style>
