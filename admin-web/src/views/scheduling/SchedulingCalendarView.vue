<script setup>
// 店长端「排班」。版式按原型的 B · 月历（`.scratch/scheduling/prototype/admin-variants.html`
// 的 `[data-variant="B"]`）：一屏是本月人手网格（每格写当天白班、夜班各几人），
// 下面一张卡是选中那天「谁上哪个班」，底下一根「还没配规则的人」的条。
//
// 视觉沿用 `public/hygiene-admin.css` 的深青墨令牌 —— 那是**共享的样式表**，不是卫生
// 模块：排班不 import 卫生的 Python 模块、不挂它的菜单，只是同一套验收台配色。
// 本期（票 02）只做「配固定班次」：轮转周期的编辑在票 04，班次表的增删改在票 11。
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'

useScopedStylesheet('/hygiene-admin.css')

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']
// 每个班次一个颜色，按排序位循环取（原型里白班薄荷、夜班水青）。N 个班次都够用。
const SHIFT_TONES = ['mint', 'aqua', 'amber', 'seal']

const loading = ref(true)
const errorText = ref('')
const shifts = ref([])
const calendar = ref(null)
const selectedDate = ref('')
const dayDetail = ref(null)
const monthValue = ref('')
const panel = ref('month') // 'month' | 'roster'
const roster = ref([])
const busyEmployeeId = ref(null)

const today = computed(() => (calendar.value && calendar.value.today) || '')
const shiftById = computed(() => {
  const index = {}
  for (const shift of shifts.value) index[shift.id] = shift
  return index
})

// 展开只铺未来 90 天（票 02 的验收项）。翻到窗口尽头那个月时，剩下一周注定是空的 ——
// 不说清楚的话，店长会把它读成「那天没人上班」。
const beyondNote = computed(() => {
  const data = calendar.value
  if (!data || !data.window_end) return ''
  const last = data.days.length ? data.days[data.days.length - 1].business_date : ''
  if (!last || last <= data.window_end) return ''
  return `只铺到 ${data.window_end}（今天起 90 天），之后的格子还没排`
})

function currentMonthValue() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function shiftMonth(value, delta) {
  const [year, month] = value.split('-').map(Number)
  const base = new Date(year, month - 1 + delta, 1)
  return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, '0')}`
}

function monthTitle(value) {
  const [year, month] = value.split('-').map(Number)
  return `${year} 年 ${month} 月`
}

function toneOf(shiftId) {
  const index = shifts.value.findIndex((item) => item.id === shiftId)
  return SHIFT_TONES[(index < 0 ? 0 : index) % SHIFT_TONES.length]
}

function toneClass(shiftId) {
  return `tone-${toneOf(shiftId)}`
}

function formatDayLabel(businessDate) {
  if (!businessDate) return ''
  const [year, month, day] = businessDate.split('-').map(Number)
  const weekday = WEEKDAYS[new Date(year, month - 1, day).getDay()]
  return `${month}/${day} 周${weekday}`
}

function formatTodayLabel() {
  if (!today.value) return ''
  // 跟格子里的日期同一套写法（「9/24 周四」），别再自己拼一遍 —— 之前那版多绕了
  // 一次 replace + 正则，结果是「今天 9/24/四」。
  return `今天 ${formatDayLabel(today.value)}`
}

// 每格的人数：第一个班次用 <b>，其余用 <i>，中间用 · 分隔（原型 B 的写法）。
function cellCounts(day) {
  return shifts.value.map((shift) => ({
    id: shift.id,
    tone: toneOf(shift.id),
    count: day.counts[String(shift.id)] || 0,
  }))
}

async function loadCalendar(month) {
  errorText.value = ''
  const value = month || monthValue.value || currentMonthValue()
  loading.value = true
  try {
    const data = await api.get('/api/scheduling/calendar', { month: value })
    calendar.value = data
    shifts.value = data.shifts || []
    monthValue.value = data.month
    // 选中那天：今天在这个月就选今天，否则选 1 号（原型 B 一进来就是「今天」那格）。
    const inMonth = (data.days || []).some((day) => day.business_date === data.today)
    selectedDate.value = inMonth && data.today ? data.today : (data.days[0] || {}).business_date || ''
    await loadDay(selectedDate.value)
  } catch (err) {
    errorText.value = err.message || '排班月历加载失败'
  } finally {
    loading.value = false
  }
}

async function loadDay(businessDate) {
  if (!businessDate) {
    dayDetail.value = null
    return
  }
  selectedDate.value = businessDate
  try {
    dayDetail.value = await api.get('/api/scheduling/day', { date: businessDate })
  } catch (err) {
    errorText.value = err.message || '这一天读不出来'
  }
}

async function loadRoster() {
  try {
    const data = await api.get('/api/scheduling/roster')
    roster.value = data.employees || []
    if (data.shifts && data.shifts.length) shifts.value = data.shifts
  } catch (err) {
    errorText.value = err.message || '名单读不出来'
  }
}

async function openPanel(next) {
  panel.value = next
  if (next === 'roster') await loadRoster()
}

// 「还没配的人」= 在职（已批准、没停用）但一条规则都没有。名单面板和底下那根
// 提示条要用同一个口径，所以只写这一份纯函数。
function needsRule(employee) {
  return !employee.rule && !employee.disabled && employee.approved
}

function countPending(employees) {
  return (employees || []).filter(needsRule).length
}

// 名单上还没配的人数 —— 进页面时单独问一次，给底下那根条用（原型 B 的 .gPend）。
const pendingCount = ref(0)
async function loadPendingCount() {
  try {
    const data = await api.get('/api/scheduling/roster')
    shifts.value = data.shifts && data.shifts.length ? data.shifts : shifts.value
    pendingCount.value = countPending(data.employees)
  } catch (err) {
    pendingCount.value = 0
  }
}

function ruleLabel(rule) {
  if (!rule || !rule.cycle || !rule.cycle.length) return '还没配'
  const parts = rule.cycle.map((id) => (id === null ? '休' : (shiftById.value[id] || {}).name || '?'))
  const unique = new Set(parts)
  if (unique.size === 1) return `固定${parts[0]}`
  return parts.join('')
}

async function setFixedShift(employee, shiftId) {
  busyEmployeeId.value = employee.id
  errorText.value = ''
  try {
    await api.put(`/api/scheduling/rules/${employee.id}`, { cycle: [shiftId] })
    await loadRoster()
    await loadCalendar(monthValue.value)
    await loadPendingCount()
  } catch (err) {
    errorText.value = err.message || '规则没存上'
  } finally {
    busyEmployeeId.value = null
  }
}

async function clearRule(employee) {
  busyEmployeeId.value = employee.id
  errorText.value = ''
  try {
    await api.delete(`/api/scheduling/rules/${employee.id}`)
    await loadRoster()
    await loadCalendar(monthValue.value)
    await loadPendingCount()
  } catch (err) {
    errorText.value = err.message || '规则没清掉'
  } finally {
    busyEmployeeId.value = null
  }
}

onMounted(async () => {
  monthValue.value = currentMonthValue()
  await loadCalendar(monthValue.value)
  await loadPendingCount()
})
</script>

<template>
  <div class="hygiene-admin sched-page">
    <div class="gB">
      <div class="gB-body">
        <div class="gB-mon">
          <b>{{ monthTitle(monthValue) }}</b>
          <span>{{ shifts.map((s) => s.name).join(' / ') }} 人数</span>
          <em>{{ formatTodayLabel() }}</em>
        </div>

        <div class="gB-bar">
          <button class="gBtn" type="button" @click="loadCalendar(shiftMonth(monthValue, -1))">‹ 上月</button>
          <button class="gBtn" type="button" @click="loadCalendar(currentMonthValue())">本月</button>
          <button class="gBtn" type="button" @click="loadCalendar(shiftMonth(monthValue, 1))">下月 ›</button>
          <button
            class="gAct"
            type="button"
            @click="openPanel(panel === 'month' ? 'roster' : 'month')"
          >{{ panel === 'month' ? '名单 ›' : '‹ 月历' }}</button>
        </div>

        <p v-if="errorText" class="gMsg">{{ errorText }}</p>

        <template v-if="panel === 'month'">
          <div class="gB-head">
            <span v-for="label in WEEKDAYS" :key="label">{{ label }}</span>
          </div>
          <div class="gB-grid">
            <div v-for="n in (calendar ? calendar.lead : 0)" :key="`lead-${n}`" class="gB-d mute"></div>
            <button
              v-for="day in (calendar ? calendar.days : [])"
              :key="day.business_date"
              type="button"
              class="gB-d"
              :class="{
                today: day.is_today,
                sel: day.business_date === selectedDate,
                mute: !!(calendar && calendar.window_end && day.business_date > calendar.window_end),
              }"
              @click="loadDay(day.business_date)"
            >
              <span class="n">{{ day.day }}</span>
              <span class="c">
                <template v-for="(item, index) in cellCounts(day)" :key="item.id">
                  <em v-if="index">·</em>
                  <b v-if="!index" :class="item.tone">{{ item.count }}</b>
                  <i v-else :class="item.tone">{{ item.count }}</i>
                </template>
              </span>
            </button>
          </div>

          <div class="gB-legend">
            <span v-for="shift in shifts" :key="shift.id">
              <i :class="toneClass(shift.id)"></i>{{ shift.name }}
            </span>
          </div>
          <p v-if="beyondNote" class="gB-note">{{ beyondNote }}</p>

          <div class="gB-card">
            <div class="gB-card-hd">
              <b>{{ formatDayLabel(selectedDate) }}</b>
              <span v-if="selectedDate && selectedDate === today">今天</span>
              <em>合计 {{ dayDetail ? dayDetail.total : 0 }} 人</em>
            </div>
            <template v-if="dayDetail">
              <div v-for="group in dayDetail.groups" :key="group.shift.id" class="gB-grp">
                <div class="gB-grp-t" :class="toneOf(group.shift.id)">
                  <span class="d"></span><b>{{ group.shift.name }}</b><i>{{ group.count }}</i>
                </div>
                <div v-if="group.names.length" class="gB-names">
                  <span v-for="name in group.names" :key="name">{{ name }}</span>
                </div>
                <p v-else class="gB-empty">这天没人排{{ group.shift.name }}</p>
              </div>
              <p v-if="dayDetail.off_count" class="gB-empty">休 {{ dayDetail.off_count }} 人</p>
            </template>
            <p v-else class="gB-empty">{{ loading ? '读取中……' : '选一天看是谁' }}</p>
          </div>

          <button class="gPend" type="button" @click="openPanel('roster')">
            <span class="n">{{ pendingCount }}</span>
            <b>个人还没配规则</b>
            <span>{{ pendingCount ? '点这里给谁配固定班' : '全员都配好了' }}</span>
            <span class="go">›</span>
          </button>
        </template>

        <template v-else>
          <div class="gB-card gB-card-plain">
            <div class="gB-card-hd">
              <b>名单 · 规则</b>
              <span>一人一条</span>
              <em>{{ roster.length }} 人</em>
            </div>
            <div v-for="employee in roster" :key="employee.id" class="gR">
              <div class="gR-main">
                <b>{{ employee.name || employee.phone }}</b>
                <span>{{ employee.job_title || '—' }}</span>
                <span v-if="employee.disabled" class="gR-off">已停用</span>
                <span v-else-if="!employee.approved" class="gR-off">待批准</span>
                <em :class="{ on: employee.rule }">{{ ruleLabel(employee.rule) }}</em>
              </div>
              <div class="gR-act">
                <button
                  v-for="shift in shifts"
                  :key="shift.id"
                  type="button"
                  class="gBtn"
                  :disabled="busyEmployeeId === employee.id"
                  @click="setFixedShift(employee, shift.id)"
                >固定{{ shift.name }}</button>
                <button
                  v-if="employee.rule"
                  type="button"
                  class="gBtn ghost"
                  :disabled="busyEmployeeId === employee.id"
                  @click="clearRule(employee)"
                >清空</button>
              </div>
            </div>
            <p v-if="!roster.length" class="gB-empty">名单是空的</p>
            <p class="gB-note">轮转周期（白白白夜夜休休）在下一张票里编；这里只配「固定一个班」。</p>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 版式照抄原型 B（`.scratch/scheduling/prototype/admin-variants.html` 的 .gB-*），
   令牌来自 /hygiene-admin.css；这里只补原型里没有的两样：月切换条、名单行。 */
.sched-page {
  position: relative;
  z-index: 1;
  min-height: 100%;
}
.gB {
  min-height: 100%;
  background: radial-gradient(90% 40% at 85% 0%, rgba(95, 214, 230, .08), transparent 60%);
  padding-bottom: 28px;
}
.gB-body { max-width: 560px; margin: 0 auto; padding: 4px 16px 22px; }
.gB-mon { display: flex; align-items: baseline; gap: 10px; padding: 2px 2px 10px; }
.gB-mon b { font-family: var(--font-song); font-size: 16px; letter-spacing: .12em; }
.gB-mon span { font-size: 10.5px; color: var(--hy-faint); }
.gB-mon em { font-style: normal; margin-left: auto; font-size: 10.5px; color: var(--hy-faint); }
.gB-bar { display: flex; align-items: center; gap: 6px; padding: 0 2px 10px; flex-wrap: wrap; }
.gMsg { margin: 0 0 10px; font-size: 12px; color: var(--hy-seal-bright); }
.gB-head { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; padding: 0 0 5px; }
.gB-head span { text-align: center; font-size: 10px; color: var(--hy-faint); letter-spacing: .06em; }
.gB-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
.gB-d {
  position: relative; height: 62px; border-radius: 10px; border: 1px solid transparent;
  background: var(--hy-surface-2); padding: 7px 6px 0; display: flex; flex-direction: column;
  gap: 5px; font: inherit; color: inherit; text-align: left; cursor: pointer;
}
.gB-d.mute { background: transparent; border-color: var(--hy-line); opacity: .35; }
.gB-d .n { font-family: var(--font-mono); font-size: 11px; color: var(--hy-muted); line-height: 1; }
.gB-d .c { display: flex; align-items: baseline; gap: 3px; font-family: var(--font-mono); font-size: 11.5px; line-height: 1; }
.gB-d .c b { font-weight: 600; }
.gB-d .c i { font-style: normal; font-weight: 600; }
.gB-d .c em { font-style: normal; color: var(--hy-faint); font-size: 9px; }
.gB-d.today { border-color: var(--hy-mint-line); background: var(--hy-mint-soft); }
.gB-d.today .n { color: var(--hy-jade); font-weight: 700; }
.gB-d.sel { border-color: var(--hy-mint); box-shadow: var(--hy-glow-mint); }
.tone-mint { color: var(--hy-mint); }
.tone-aqua { color: var(--hy-aqua); }
.tone-amber { color: var(--hy-amber); }
.tone-seal { color: var(--hy-seal-bright); }
.gB-legend { display: flex; align-items: center; gap: 12px; margin-top: 11px; font-size: 10.5px; color: var(--hy-faint); padding: 0 2px; flex-wrap: wrap; }
.gB-legend i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; vertical-align: middle; background: currentColor; }
.gB-legend span { color: var(--hy-faint); }
.gB-card { margin-top: 14px; border: 1px solid var(--hy-line); border-radius: var(--hy-radius-lg); background: var(--hy-surface); padding: 13px 14px; }
.gB-card-plain { margin-top: 0; }
.gB-card-hd { display: flex; align-items: baseline; gap: 9px; padding-bottom: 11px; border-bottom: 1px solid var(--hy-line); }
.gB-card-hd b { font-family: var(--font-song); font-size: 15px; letter-spacing: .08em; }
.gB-card-hd span { font-size: 11px; color: var(--hy-muted); }
.gB-card-hd em { margin-left: auto; font-style: normal; font-size: 10.5px; color: var(--hy-faint); }
.gB-grp { padding-top: 11px; }
.gB-grp-t { display: flex; align-items: center; gap: 7px; font-size: 11px; margin-bottom: 7px; }
.gB-grp-t .d { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.gB-grp-t b { font-family: var(--font-song); font-size: 12.5px; letter-spacing: .08em; }
.gB-grp-t i { font-style: normal; font-family: var(--font-mono); color: var(--hy-ink); }
.gB-names { display: flex; flex-wrap: wrap; gap: 6px; }
.gB-names span { font-size: 11.5px; color: var(--hy-ink); background: var(--hy-surface-2); border: 1px solid var(--hy-line); border-radius: 7px; padding: 3px 8px; }
.gB-empty { margin: 6px 0 0; font-size: 11.5px; color: var(--hy-faint); }
.gB-note { margin: 14px 0 0; font-size: 11px; color: var(--hy-faint); line-height: 1.7; }
.gPend {
  margin-top: 12px; width: 100%; display: flex; align-items: center; gap: 9px; padding: 10px 12px;
  border: 1px solid var(--hy-amber-line); border-radius: var(--hy-radius-md);
  background: var(--hy-amber-soft); font-size: 12.5px; color: var(--hy-ink); font: inherit;
  text-align: left; cursor: pointer;
}
.gPend .n { width: 20px; height: 20px; flex: none; border-radius: 6px; display: grid; place-items: center; font-family: var(--font-mono); font-size: 11.5px; font-weight: 700; color: #2a1c05; background: var(--hy-amber); }
.gPend b { font-weight: 600; color: var(--hy-ink); font-size: 12.5px; }
.gPend span { color: var(--hy-muted); font-size: 12.5px; }
.gPend .go { margin-left: auto; color: var(--hy-amber); font-size: 14px; }
.gBtn {
  font: inherit; font-size: 11px; color: var(--hy-muted); background: var(--hy-surface-2);
  border: 1px solid var(--hy-line); border-radius: 999px; padding: 4px 10px; cursor: pointer;
}
.gBtn:disabled { opacity: .45; cursor: default; }
.gBtn.ghost { color: var(--hy-faint); }
.gAct {
  margin-left: auto; font: inherit; font-size: 11px; color: var(--hy-mint);
  border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
  border-radius: 999px; padding: 4px 10px; cursor: pointer;
}
.gR { padding-top: 11px; }
.gR + .gR { border-top: 1px solid var(--hy-line); margin-top: 11px; }
.gR-main { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.gR-main b { font-size: 13px; }
.gR-main span { font-size: 11px; color: var(--hy-faint); }
.gR-main .gR-off { color: var(--hy-seal-bright); }
.gR-main em { margin-left: auto; font-style: normal; font-family: var(--font-mono); font-size: 11px; color: var(--hy-faint); }
.gR-main em.on { color: var(--hy-mint); }
.gR-act { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 7px; }
</style>
