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
      <div>
        <p class="hKicker">人事</p>
        <h1>{{ TITLE }}</h1>
        <p class="hSub">
          点某一天就把它标成法定节假日并写一个名称（国庆、春节），再点它一次就能取消。
          只标法定节假日，<b>不标调休上班日</b>：法定节假日补休只认这里标出来的日子，
          代码里没有一张写死的年度表。
        </p>
      </div>
      <div class="hYear">
        <button class="btn" type="button" :disabled="busy" @click="goYear(-1)">‹ {{ year - 1 }}</button>
        <b>{{ year }} 年</b>
        <button class="btn" type="button" :disabled="busy" @click="goYear(1)">{{ year + 1 }} ›</button>
      </div>
    </header>

    <p v-if="errorText" class="hErr" role="alert">{{ errorText }}</p>
    <p v-else-if="receipt" class="hOk" role="status">{{ receipt }}</p>

    <!-- 选中的那一格：没标过的是「写名称」，标过的是「改名字 / 取消标记」。 -->
    <section v-if="selected" class="hCard hEditor">
      <div class="hCard-hd">
        <h2>{{ selected }}</h2>
        <span v-if="selectedName" class="hCount">已标记</span>
        <span v-else class="hCount hCount-new">新标记</span>
        <button class="btn hClose" type="button" @click="selected = ''">收起</button>
      </div>
      <label class="hField">
        <span>节假日名称</span>
        <input v-model="draft" type="text" placeholder="比如：国庆" @keyup.enter="save" />
      </label>
      <p v-if="draftHint" class="hLead hBad">{{ draftHint }}</p>
      <p class="hLead">
        1~{{ MAX_HOLIDAY_NAME }} 个字。同一天重复标记是覆盖这个名称，不会多出一条记录。
      </p>
      <div class="hAct">
        <button class="btn btn-primary" type="button" :disabled="busy" @click="save">标记</button>
        <button v-if="selectedName" class="btn" type="button" :disabled="busy" @click="remove">
          取消标记
        </button>
      </div>
    </section>

    <p v-if="state === 'loading'" class="hHint">正在读这一年的日历…</p>
    <p v-else-if="!markedCount" class="hHint">
      {{ year }} 年还一天都没标。左边没有「法定节假日」的日子，补休一分都不给。
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
  </div>
</template>

<style scoped>
/* 版式照人事组其余页面（深青墨底 + 一张卡一个话题）。令牌一律用**语义名**
   （`--ink` / `--surface-1` / `--danger-bright` …，定义在 `src/styles/theme.workbench.css`
   的 `html[data-workbench]`）：`--hy-*` 是兼容层，新文件不用它 —— 设计护栏
   （`src/styles/__tests__/designGuard.test.js`）对没进存量快照的文件按 strict 卡这一条。 */
.hr-holidays { padding: var(--sp-4) var(--sp-4) var(--sp-8); max-width: 1120px; margin: 0 auto; }
.hTop { display: flex; align-items: flex-start; gap: var(--sp-3); flex-wrap: wrap; margin-bottom: var(--sp-4); }
.hTop h1 { margin: var(--sp-1) 0 var(--sp-1); font-size: var(--fs-20); color: var(--ink); }
.hKicker { margin: 0; font-size: var(--fs-11); letter-spacing: var(--ls-4); color: var(--ink-faint); }
.hSub { margin: 0; max-width: 640px; font-size: var(--fs-12); line-height: 1.7; color: var(--ink-dim); }
.hSub b { color: var(--warn); }
.hYear { margin-left: auto; display: flex; align-items: center; gap: var(--sp-2); }
.hYear b { font-family: var(--font-mono); font-size: var(--fs-13); color: var(--ink); }
.hHint { margin: 0; font-size: var(--fs-12); color: var(--ink-dim); }
.hErr {
  margin: 0 0 var(--sp-3); padding: var(--sp-2) var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--danger-bright); background: var(--danger-soft);
  font-size: var(--fs-12); color: var(--danger-bright);
}
.hOk {
  margin: 0 0 var(--sp-3); padding: var(--sp-2) var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--line-strong); background: var(--ok-soft);
  font-size: var(--fs-12); color: var(--ok);
}
.hCard {
  margin-bottom: var(--sp-3); padding: var(--sp-3); border: 1px solid var(--line);
  border-radius: var(--r-2); background: var(--surface-1);
}
.hCard-hd { display: flex; align-items: center; gap: var(--sp-2); margin-bottom: var(--sp-1); }
.hCard-hd h2 { margin: 0; font-size: var(--fs-14); color: var(--ink); }
.hCount {
  padding: 1px var(--sp-2); border-radius: var(--r-full); font-size: var(--fs-11);
  color: var(--action); border: 1px solid var(--line-strong); background: var(--action-soft);
}
.hCount-new { color: var(--ink-dim); border-color: var(--line); background: transparent; }
.hClose { margin-left: auto; }
.hField { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-12); color: var(--ink-dim); }
.hField input {
  width: 220px; padding: var(--sp-1) var(--sp-2); font-size: var(--fs-13); color: var(--ink);
  border: 1px solid var(--line-control); border-radius: var(--r-1); background: var(--surface-2);
}
.hLead { margin: var(--sp-2) 0 0; font-size: var(--fs-12); line-height: 1.7; color: var(--ink-dim); }
.hBad { color: var(--danger-bright); }
.hAct { margin-top: var(--sp-3); display: flex; align-items: center; gap: var(--sp-3); }
/* 一年 12 个月：窄屏一行一张两张，宽屏一行四张（跟卡片最小宽度走）。 */
.hGrid {
  display: grid; gap: var(--sp-3);
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
}
.hMonth {
  padding: var(--sp-2) var(--sp-3) var(--sp-3); border: 1px solid var(--line);
  border-radius: var(--r-2); background: var(--surface-1);
}
.hMonth h2 { margin: 0 0 var(--sp-1); font-size: var(--fs-12); color: var(--ink); }
.hWeekdays, .hWeek { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
.hWeekdays span {
  text-align: center; font-size: var(--fs-11); color: var(--ink-faint); padding-bottom: var(--sp-1);
}
/* 一天一格：`min-height` 取触控下限 `--tap`（44px）—— 这一页的主要操作就是点格子，
   手指按不准就等于点错一天。格子窄时七列正好占满一屏宽。 */
.hCell {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: var(--tap); padding: 2px 0; gap: 1px;
  border: 1px solid transparent; border-radius: var(--r-1); background: transparent;
  font-size: var(--fs-11); color: var(--ink-dim); cursor: pointer;
}
.hCell:hover { border-color: var(--line-strong); color: var(--ink); }
.hCell-blank { cursor: default; }
.hCell b { font-weight: 500; font-family: var(--font-mono); font-size: var(--fs-13); }
.hCell em {
  font-style: normal; font-size: var(--fs-11); line-height: 1.1; max-width: 100%;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--surface-0);
}
/* 已标记的那一天：实底 + 名字写在格子里 —— 一眼看得出是哪个节。 */
.hCell.is-marked {
  background: var(--warn); border-color: transparent; color: var(--surface-0);
}
.hCell.is-picked { border-color: var(--action); box-shadow: 0 0 0 1px var(--focus-ring); }
</style>
