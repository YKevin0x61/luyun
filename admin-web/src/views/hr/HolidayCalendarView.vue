<script setup>
// 管理端「节假日日历」（考勤面，票 02）：一个月一张网格，点某一天把它标成
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
//
// 2026-10-29（用户）：一屏只放**一个月**、默认当月，面板头左右翻月；接口仍按年取，
// 翻月是同一年内的纯前端切换，翻过年界才重读。
//
// 同一天（用户）：编辑带挂在**日历下面**（网格之后、同一张面板里），不再压在上面占位；
// 手机上点第三行往下时带子可能落在视口外，打开后把它滚进来。
import { computed, nextTick, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { workbenchDocumentTitle } from '../../utils/workbenchCopy'
import {
  MAX_HOLIDAY_NAME,
  WEEKDAYS,
  holidayMap,
  monthGrid,
  nameError,
} from '../../utils/holidayCalendar'

const PATH = '/api/attendance/holidays'
const TITLE = '节假日日历'

// 打开就是今年这个月；页头翻年、面板头翻月，两个按钮操作同一份 year/month。
const now = new Date()
const year = ref(now.getFullYear())
const month = ref(now.getMonth() + 1)
const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
const receipt = ref('')
const marked = ref({}) // 'YYYY-MM-DD' → 名称
const selected = ref('') // 正在编辑的那一天（'' = 没选）
const draft = ref('')
const busy = ref(false)
const editorBar = ref(null) // 网格下面那条编辑带（滚动定位它）

const grid = computed(() => monthGrid(year.value, month.value))
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

/** 翻年：换年份重读，顺手把正在编辑的那一格收起来（它属于上一年）。月份不动。 */
async function goYear(step) {
  year.value += step
  selected.value = ''
  draft.value = ''
  receipt.value = ''
  await load()
}

/** 翻月：1 月往左回到上一年 12 月，12 月往右进下一年 1 月。
 *
 *  同一年内只是换一格网格，接口那一年的数据已经在手上，不必再读；跨年才 `load()`。
 */
async function goMonth(step) {
  if (busy.value) return
  let next = month.value + step
  let nextYear = year.value
  if (next < 1) {
    next = 12
    nextYear -= 1
  } else if (next > 12) {
    next = 1
    nextYear += 1
  }
  const yearChanged = nextYear !== year.value
  month.value = next
  selected.value = ''
  draft.value = ''
  receipt.value = ''
  if (yearChanged) {
    year.value = nextYear
    await load()
  }
}

function pick(day) {
  if (busy.value) return
  selected.value = day
  draft.value = marked.value[day] || ''
  errorText.value = ''
  receipt.value = ''
  // 编辑带在网格下面：手机上点下面的日子时它可能刚好在视口外，滚一下免得以为没反应。
  nextTick(() => {
    const el = editorBar.value
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  })
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
          一屏一个月，默认当月：面板头左右箭头翻上 / 下个月（翻到年界会自己换年）。
          点某一天就把它标成法定节假日并写一个名称（国庆、春节），再点它一次就能取消。
          只标法定节假日，<b>不标调休上班日</b>：法定节假日补休只认这里标出来的日子，
          代码里没有一张写死的年度表。
        </p>
      </details>
    </header>

    <p v-if="errorText" class="hErr" role="alert">{{ errorText }}</p>
    <p v-else-if="receipt" class="hOk" role="status">{{ receipt }}</p>

    <p v-if="state === 'loading'" class="hHint">正在读这一年的日历…</p>

    <section class="hPanel hYearPanel">
      <div class="hPanel-hd">
        <i class="hLed sig"></i>
        <div class="hMonthNav">
          <button class="hNav" type="button" :disabled="busy" aria-label="上个月" title="上个月" @click="goMonth(-1)">‹</button>
          <b class="hMonthNow">{{ year }} 年 {{ month }} 月</b>
          <button class="hNav" type="button" :disabled="busy" aria-label="下个月" title="下个月" @click="goMonth(1)">›</button>
        </div>
        <span class="hPanel-hint">全年已标 {{ markedCount }} 天</span>
      </div>
      <p v-if="!markedCount && state === 'ready'" class="hEmptyLine">
        这一年还一天都没标：没有「法定节假日」的日子，补休一分都不给。点下面任意一天开始标。
      </p>
      <div class="hGrid">
        <div class="hWeekdays">
          <span v-for="day in WEEKDAYS" :key="day">{{ day }}</span>
        </div>
        <div v-for="(week, index) in grid.weeks" :key="index" class="hWeek">
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
      </div>

      <!-- 选中的那一格：编辑带挂在网格**下面**（用户 2026-10-29 定；压在日历上面会
           让整块日历往下跳）。输入 + 标记 / 取消标记 / 收起都在这一条里。 -->
      <section v-if="selected" ref="editorBar" class="hEditorBar">
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
    </section>
  </div>
</template>

<style scoped>
/* B 案 v3（2026-10-17 版式审查）+ 2026-10-29 单月改版：1052 容器；页头 3 行
   （kicker / H1 + 年切换 / 怎么用），右栏两块并进页头（编辑带一行）；主面板只放
   **一个月**，网格收到 600 居中，格子从 32 抬到 64×48 —— 一屏看得清、手指也好点。
   已标记天数只出现在面板头。 */
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
/* 编辑带：挂在网格**下面**的一条（色点 / 日期 / 状态 / 名称 / 提示 / 动作），
   不再压在上面占位。分隔线用面板同一条线，底色给一点「正在改」的提示。 */
.hEditorBar {
  display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap;
  padding: 10px 14px; border-top: 1px solid var(--line);
  background: var(--action-soft);
}
.hEditorDay { font-family: var(--font-mono); font-size: 12px; color: var(--ink); }
.hEditorHint { font-size: 9.5px; color: var(--ink-faint); }
.hField { flex: 0 1 220px; min-width: 0; display: flex; }
.hField input {
  width: 100%; min-height: var(--tap); padding: var(--sp-1) var(--sp-2);
  font-family: var(--font-sans); font-size: var(--fs-13); color: var(--ink);
  border: 1px solid var(--line-control); border-radius: var(--r-1); background: var(--surface-1);
}
.hBad { font-size: 10px; color: var(--danger-bright); }
.hAct { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.hAct .btn { border-radius: var(--r-1); min-height: var(--tap); }
/* 唯一的主面板：一个月的网格。 */
.hPanel {
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: none; overflow: hidden;
}
.hPanel-hd {
  display: flex; align-items: center; gap: 9px; flex-wrap: wrap;
  padding: 9px 14px; border-bottom: 1px solid var(--line);
}
/* 面板头左侧：‹ 2026 年 10 月 ›。翻月是这一页的主操作，所以放在标题位。 */
.hMonthNav { display: flex; align-items: center; gap: 6px; }
.hNav {
  width: var(--tap); height: var(--tap); display: grid; place-items: center;
  padding: 0; border: 1px solid var(--line-strong); border-radius: var(--r-1);
  background: var(--surface-2); color: var(--ink-dim);
  font-family: var(--font-sans); font-size: 15px; line-height: 1; cursor: pointer;
}
.hNav:hover:not(:disabled) { color: var(--ink); border-color: var(--action); }
.hNav:disabled { opacity: .45; cursor: not-allowed; }
.hMonthNow {
  min-width: 110px; text-align: center;
  font-family: var(--font-mono); font-size: 12.5px; line-height: 1; color: var(--ink);
}
.hPanel-hint { margin-left: auto; font-family: var(--font-mono); font-size: 9.5px; line-height: 1; color: var(--ink-faint); }
.hEmptyLine {
  margin: 0; padding: 10px 14px; border-bottom: 1px solid var(--line);
  font-size: 11px; color: var(--ink-faint);
}
/* 单月网格：7 列铺满 600（桌面居中、手机自然撑满），宽屏不再挤成一小块。 */
.hGrid {
  width: 100%; max-width: 600px; margin: 0 auto;
  padding: 8px 10px 16px;
}
.hWeekdays, .hWeek { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 2px 0; }
.hWeekdays span {
  text-align: center; font-family: var(--font-sans); font-size: 10px; line-height: 1;
  letter-spacing: .04em; color: var(--ink-faint); padding: 7px 0 5px;
}
/* 一天一格：64×48（触摸下限 44 之上），伪元素再扩一圈点击区。 */
.hCell {
  position: relative; width: 100%; max-width: 64px; height: 48px; margin: 0 auto;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 0; padding: 0;
  border: 1px solid transparent; border-radius: var(--r-1); background: transparent;
  font-family: var(--font-sans); font-size: 9px; color: var(--ink-dim); cursor: pointer;
}
.hCell::after { content: ""; position: absolute; inset: -2px; }
.hCell:hover { border-color: var(--line-strong); color: var(--ink); }
.hCell-blank { cursor: default; }
.hCell b { font-weight: 500; font-family: var(--font-mono); font-size: 14px; line-height: 1.15; }
.hCell em {
  font-style: normal; font-size: 9.5px; line-height: 1.15; max-width: 60px;
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
  .hTop { grid-template-columns: 1fr; grid-template-areas: "kick" "title" "sub"; }
  .hTop h1 { font-size: 22px; }
  .hYear { margin-left: 0; }
}
@media (max-width: 720px) {
  /* 编辑带在窄屏排成四段：日期行 → 输入框整行 → 按钮行 → 小字提示。
     560 的断点管的是月份导航与网格留白，编辑带要早点折行（616 宽下挤一行太散）。
     720 是设计护栏认可的断点档（430 / 560 / 720 / 900），别自己另开一个。 */
  .hField { flex: 1 1 100%; order: 1; }
  .hAct { order: 2; }
  .hEditorHint { order: 3; flex-basis: 100%; }
  .hBad { order: 4; flex-basis: 100%; }
}
@media (max-width: 560px) {
  /* 窄屏别让面板头挤成两行：月份导航让出一点宽度，提示缩到下一行也放得下。 */
  .hMonthNow { min-width: 92px; font-size: 11.5px; }
  .hGrid { padding: 6px 8px 12px; }
  .hCell { height: 48px; }
}
</style>
