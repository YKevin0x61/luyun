<script setup>
// 管理端「节假日日历」（考勤面，票 02）：一年一个网格（12 个月），点某一天把它标成
// 法定节假日并写一个名称（国庆、春节）。
//
// **只标法定节假日、不标调休上班日** —— 「法定节假日补休」（ADR 0113）只用得到前者。
// 这张日历是「哪天算法定节假日」的**唯一依据**，所以这一页只给管理端这一档
// （`pageRoutes.json` 里 audience: admin），接口那一侧也是 `require_session`。
//
// 交互（票面：点一天标上、再点它一次取消）：
//   * 点**没标过**的那一天 → 就地开一格输入，写名称后「标记」（点一次不够 —— 名称是必填的，
//     「标记必须写名称」是验收 3）；
//   * 点**标过**的那一天 → 同一格里给出名称与「取消标记」，也可改名字再点「标记」覆盖
//     （同一天重复标记是覆盖那一行，不是新增一条 —— 服务端就是这么落库的）。
// 一句话：点第二次就把这一天交回你处理（取消 / 改名），不再是"点一下就没"。
// 标记与取消成功后按**这一年**重读一次，页面立刻就是新的，不用刷新。
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import {
  MAX_HOLIDAY_NAME,
  WEEKDAYS,
  holidayMap,
  nameError,
  yearGrid,
} from '../../utils/holidayCalendar'

const PATH = '/api/attendance/holidays'
const TITLE = '节假日日历'

// 打开就是今年（页面上不提供月份选择，只翻年）。
const year = ref(new Date().getFullYear())
const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
const receipt = ref('')
const marked = ref({}) // 'YYYY-MM-DD' → 名称
const selected = ref('') // 正在编辑的那一天（'' = 没选）
const draft = ref('')
const busy = ref(false)

const months = computed(() => yearGrid(year.value))
const markedCount = computed(() => Object.keys(marked.value).length)
const selectedName = computed(() => marked.value[selected.value] || '')
// 正在输入的那个名称的问题（超长时当场就看得见）。空的只在点「标记」时拒 ——
// 一开输入框就飘一条红字太吵。
const draftHint = computed(() =>
  selected.value && draft.value ? nameError(draft.value) : '',
)

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    const body = await api.get(PATH, { year: year.value })
    marked.value = holidayMap(body.days)
    state.value = 'ready'
  } catch (err) {
    errorText.value = err.message || '这一年的日历读不出来'
    state.value = 'error'
  }
}

/** 翻年：换年份重读，顺手把正在编辑的那一格收起来（它属于上一年）。 */
async function goYear(step) {
  year.value += step
  selected.value = ''
  draft.value = ''
  receipt.value = ''
  await load()
}

function pick(day) {
  if (busy.value) return
  selected.value = day
  draft.value = marked.value[day] || ''
  errorText.value = ''
  receipt.value = ''
}

/** 标记 / 覆盖：名称为空或超长当场拦住，服务端那道闸是同一口径。 */
async function save() {
  if (busy.value || !selected.value) return
  const problem = nameError(draft.value)
  if (problem) {
    errorText.value = problem
    return
  }
  busy.value = true
  errorText.value = ''
  receipt.value = ''
  const day = selected.value
  try {
    await api.put(`${PATH}/${day}`, { name: draft.value.trim() })
    await load()
    receipt.value = `${day} 记成「${marked.value[day] || draft.value.trim()}」`
    selected.value = ''
    draft.value = ''
  } catch (err) {
    // 先重读、最后再说那句话：反过来的话 `load()` 开头的清空会把服务端的提示抹掉。
    const message = err.message || '没记上'
    await load()
    errorText.value = message
  } finally {
    busy.value = false
  }
}

async function remove() {
  if (busy.value || !selected.value) return
  busy.value = true
  errorText.value = ''
  receipt.value = ''
  const day = selected.value
  try {
    await api.delete(`${PATH}/${day}`)
    await load()
    receipt.value = `${day} 不再是法定节假日`
    selected.value = ''
    draft.value = ''
  } catch (err) {
    const message = err.message || '没取消掉'
    await load()
    errorText.value = message
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  // 名字与 `pageRoutes.json` 里 `/workbench/hr/holidays` 那一行同步。
  document.title = workbenchDocumentTitle(TITLE)
  load()
})
</script>

<template>
  <div class="hygiene-admin hr-holidays">
    <header class="hTop">
      <p class="hKicker">人事 · 考勤</p>
      <div class="hTitleRow">
        <h1>{{ TITLE }}</h1>
        <div class="hYear">
          <button class="btn hAction" type="button" :disabled="busy" @click="goYear(-1)">‹ {{ year - 1 }}</button>
          <span class="hYearNow">{{ year }} 年</span>
          <button class="btn hAction" type="button" :disabled="busy" @click="goYear(1)">{{ year + 1 }} ›</button>
        </div>
      </div>
      <details class="hHelp">
        <summary>怎么用</summary>
        <p class="hSub">
          点某一天就把它标成法定节假日并写一个名称（国庆、春节），再点它一次就能取消。
          只标法定节假日，<b>不标调休上班日</b>：法定节假日补休只认这里标出来的日子，
          代码里没有一张写死的年度表。
        </p>
      </details>
    </header>

    <p v-if="errorText" class="hErr" role="alert">{{ errorText }}</p>
    <p v-else-if="receipt" class="hOk" role="status">{{ receipt }}</p>

    <!-- 选中的那一格：一行编辑带（输入 + 标记 / 取消），跟页头同宽。 -->
    <section v-if="selected" class="hEditorBar">
      <i class="hLed sig"></i>
      <b class="hEditorDay">{{ selected }}</b>
      <span v-if="selectedName" class="hCount">已标记</span>
      <span v-else class="hCount hCount-new">新标记</span>
      <label class="hField">
        <input v-model="draft" type="text" placeholder="比如：国庆" @keyup.enter="save" />
      </label>
      <span v-if="draftHint" class="hBad">{{ draftHint }}</span>
      <span class="hEditorHint">1~{{ MAX_HOLIDAY_NAME }} 个字；同名覆盖</span>
      <div class="hAct">
        <button class="btn btn-primary" type="button" :disabled="busy" @click="save">标记</button>
        <button v-if="selectedName" class="btn" type="button" :disabled="busy" @click="remove">
          取消标记
        </button>
        <button class="btn" type="button" @click="selected = ''">收起</button>
      </div>
    </section>

    <p v-if="state === 'loading'" class="hHint">正在读这一年的日历…</p>

    <section class="hPanel hYearPanel">
      <div class="hPanel-hd">
        <i class="hLed sig"></i>
        <h2>全年 12 个月</h2>
        <span class="hPanel-hint">{{ year }} 年 · 已标 {{ markedCount }} 天</span>
      </div>
      <p v-if="!markedCount && state === 'ready'" class="hEmptyLine">
        这一年还一天都没标：没有「法定节假日」的日子，补休一分都不给。点下面任意一天开始标。
      </p>
      <div class="hGrid">
        <section v-for="month in months" :key="month.month" class="hMonth">
          <h2>{{ month.label }}</h2>
          <div class="hWeekdays">
            <span v-for="day in WEEKDAYS" :key="day">{{ day }}</span>
          </div>
          <div v-for="(week, index) in month.weeks" :key="index" class="hWeek">
            <template v-for="(day, cell) in week" :key="cell">
              <span v-if="!day" class="hCell hCell-blank" aria-hidden="true"></span>
              <button
                v-else
                class="hCell"
                :class="{ 'is-marked': !!marked[day], 'is-picked': selected === day }"
                type="button"
                :title="marked[day] ? `${day} ${marked[day]}（点它取消）` : `${day}（点它标成法定节假日）`"
                @click="pick(day)"
              >
                <b>{{ Number(day.slice(-2)) }}</b>
                <em v-if="marked[day]">{{ marked[day] }}</em>
              </button>
            </template>
          </div>
        </section>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* B 案 v3（2026-10-17 版式审查）：1052 容器；页头 3 行（kicker / H1 + 年切换 / 怎么用），
   右栏两块并进页头（编辑带一行）；12 个月一屏 4 列 × 3 行；格子 32×32，点击区靠伪元素
   扩到 40。已标记天数只出现在面板头。 */
.hr-holidays {
  display: flex; flex-direction: column; gap: var(--sp-4);
  padding: 18px clamp(16px, 4vw, 32px) calc(40px + var(--safe-b));
  width: 100%; max-width: 1052px; margin: 0 auto; font-family: var(--font-sans);
}
/* 页头 3 行网格：kicker / H1 + 年切换（同基线）/ 说明折叠。 */
.hTop {
  display: grid; grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "kick kick" "title action" "sub sub";
  align-items: end; column-gap: var(--sp-4); row-gap: 7px;
}
.hKicker {
  grid-area: kick; margin: 0; font-family: var(--font-sans); font-size: 9.5px; line-height: 1;
  font-weight: 500; letter-spacing: .26em; color: var(--ink-faint);
}
.hTitleRow { grid-area: title; display: flex; align-items: baseline; gap: var(--sp-4); min-width: 0; }
.hTop h1 {
  margin: 0; font-family: var(--font-sans); font-size: 26px; line-height: 1;
  font-weight: 600; letter-spacing: .01em; color: var(--ink);
}
.hYear { margin-left: auto; display: flex; align-items: baseline; gap: var(--sp-2); }
.hYearNow { font-family: var(--font-mono); font-size: 12px; color: var(--ink); }
.hAction { border-radius: var(--r-1); min-height: var(--tap); }
.hHelp { grid-area: sub; }
.hHelp summary {
  display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  font-size: 10px; letter-spacing: .06em; color: var(--ink-faint);
}
.hHelp summary::before {
  content: "?"; width: 14px; height: 14px; display: grid; place-items: center;
  border: 1px solid var(--line-strong); border-radius: 50%; font-size: 8px;
}
.hSub { margin: 8px 0 0; max-width: 760px; font-size: var(--fs-12); line-height: 1.7; color: var(--ink-dim); }
.hSub b { color: var(--warn); }
.hLed { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; }
.hLed.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }
.hHint { margin: 0; font-size: 12.5px; color: var(--ink-dim); }
.hErr {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--danger); background: var(--danger-soft);
  font-size: 12.5px; color: var(--danger-bright);
}
.hOk {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--line-strong); background: var(--ok-soft);
  font-size: 12.5px; color: var(--ok);
}
/* 编辑带：一行（色点 / 日期 / 状态 / 名称 / 提示 / 动作）。 */
.hEditorBar {
  display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap;
  padding: 8px 14px; border: 1px solid var(--action); border-radius: var(--r-2);
  background: var(--action-soft);
}
.hEditorDay { font-family: var(--font-mono); font-size: 12px; color: var(--ink); }
.hEditorHint { font-size: 9.5px; color: var(--ink-faint); }
.hField input {
  width: 220px; min-height: var(--tap); padding: var(--sp-1) var(--sp-2);
  font-family: var(--font-sans); font-size: var(--fs-13); color: var(--ink);
  border: 1px solid var(--line-control); border-radius: var(--r-1); background: var(--surface-1);
}
.hBad { font-size: 10px; color: var(--danger-bright); }
.hAct { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.hAct .btn { border-radius: var(--r-1); min-height: var(--tap); }
/* 唯一的主面板：全年 12 个月。 */
.hPanel {
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: none; overflow: hidden;
}
.hPanel-hd {
  display: flex; align-items: center; gap: 9px;
  padding: 11px 14px; border-bottom: 1px solid var(--line);
}
.hPanel-hd h2 {
  margin: 0; font-family: var(--font-sans); font-size: 12.5px; line-height: 1;
  font-weight: 600; letter-spacing: .06em; color: var(--ink);
}
.hPanel-hint { margin-left: auto; font-family: var(--font-mono); font-size: 9.5px; line-height: 1; color: var(--ink-faint); }
.hEmptyLine {
  margin: 0; padding: 10px 14px; border-bottom: 1px solid var(--line);
  font-size: 11px; color: var(--ink-faint);
}
/* 12 个月：一屏 4 列 × 3 行。 */
.hGrid {
  display: grid; gap: 12px; padding: 12px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.hMonth {
  padding: 0 0 4px; border: 1px solid var(--line);
  border-radius: var(--r-1); background: var(--surface-2); min-width: 0;
}
.hMonth h2 {
  margin: 0; padding: 6px 8px; border-bottom: 1px solid var(--line);
  display: flex; align-items: center; gap: 6px;
  font-family: var(--font-sans); font-size: 9.5px; font-weight: 600;
  letter-spacing: .12em; color: var(--ink);
}
.hMonth h2::before {
  content: ""; width: 6px; height: 6px; flex: none; border-radius: 50%;
  background: var(--action);
}
.hWeekdays, .hWeek { display: grid; grid-template-columns: repeat(7, 1fr); gap: 0; padding: 0 4px; }
.hWeekdays span {
  text-align: center; font-family: var(--font-sans); font-size: 8px; line-height: 1;
  letter-spacing: .04em; color: var(--ink-faint); padding: 5px 0 3px;
}
/* 一天一格：32×32（审查口径），伪元素把点击区扩到 40。 */
.hCell {
  position: relative; width: 32px; height: 32px; margin: 0 auto;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 0; padding: 0;
  border: 1px solid transparent; border-radius: var(--r-1); background: transparent;
  font-family: var(--font-sans); font-size: 8px; color: var(--ink-dim); cursor: pointer;
}
.hCell::after { content: ""; position: absolute; inset: -4px; }
.hCell:hover { border-color: var(--line-strong); color: var(--ink); }
.hCell-blank { cursor: default; }
.hCell b { font-weight: 500; font-family: var(--font-mono); font-size: 10px; line-height: 1.1; }
.hCell em {
  font-style: normal; font-size: 7px; line-height: 1.1; max-width: 32px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--warn);
}
.hCell.is-marked { background: var(--warn-soft); border-color: var(--warn); color: var(--warn); }
.hCell.is-picked { border-color: var(--action); box-shadow: 0 0 0 1px var(--focus-ring); }
.hCount {
  padding: 1px var(--sp-2); border-radius: var(--r-full); font-family: var(--font-mono);
  font-size: 9px; color: var(--action);
  border: 1px solid var(--line-strong); background: var(--surface-1);
}
.hCount-new { color: var(--ink-dim); border-color: var(--line); background: transparent; }
@media (max-width: 900px) {
  .hGrid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .hTop { grid-template-columns: 1fr; grid-template-areas: "kick" "title" "sub"; }
  .hTop h1 { font-size: 22px; }
  .hYear { margin-left: 0; }
}
@media (max-width: 560px) {
  .hGrid { grid-template-columns: 1fr; }
}
</style>
