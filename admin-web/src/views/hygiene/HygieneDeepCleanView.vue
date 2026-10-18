<script setup>
import { computed, onMounted, ref } from 'vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import LuyunTimePicker from '../../components/ui/LuyunTimePicker.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { HYGIENE_WEEKDAYS, hygieneWeekdayLabel } from '../../utils/hygieneCopy'
import { deepCleanShotUrl } from '../../utils/hygieneMarkup'
import { deepQueueKey, nextAfterRemove } from '../../utils/hygieneWorkFlow'

const items = ref([])
const days = ref([])
const queue = ref([])
const loading = ref(true)
const errorText = ref('')
const weekday = ref(0)
const newName = ref('')
const adding = ref(false)
const clock = ref('21:30')
const savingClock = ref(false)
const clockHint = ref('')
const busy = ref(false)
const selected = ref(null)
const review = ref(null)
const lastPassed = ref(null)
const teachingHint = ref('')
const fromDate = ref('')
const toDate = ref('')
const removeTarget = ref(null)

const weekdayItems = computed(() => {
  return items.value.filter((item) => item.weekday === weekday.value)
})

onMounted(() => {
  const range = currentWeekRange()
  fromDate.value = range.from
  toDate.value = range.to
  weekday.value = pythonWeekday(new Date())
  refreshPage()
})

useHygieneRealtime({
  id: 'hygiene-admin-deep',
  resources: ['deep', 'settings'],
  pull: refreshPage,
})

function toIsoDate(date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function pythonWeekday(date) {
  const day = date.getDay()
  return day === 0 ? 6 : day - 1
}

function currentWeekRange() {
  const now = new Date()
  const mondayOffset = pythonWeekday(now) * -1
  const monday = new Date(now)
  monday.setHours(0, 0, 0, 0)
  monday.setDate(now.getDate() + mondayOffset)
  const sunday = new Date(monday)
  sunday.setDate(monday.getDate() + 6)
  return { from: toIsoDate(monday), to: toIsoDate(sunday) }
}

function toHhmm(raw) {
  const text = String(raw || '').trim()
  return text.length >= 5 ? text.slice(0, 5) : text
}

async function refreshPage() {
  loading.value = true
  errorText.value = ''
  try {
    await Promise.all([loadItems(), loadClock(), loadCalendar(), loadQueue()])
  } catch (err) {
    errorText.value = err.message || '无法加载专项卫生'
  } finally {
    loading.value = false
  }
}

async function loadItems() {
  const data = await api.get('/api/hygiene/admin/deep-clean/items')
  items.value = data.items || []
}

async function loadClock() {
  const data = await api.get('/api/hygiene/admin/deep-clean/clock')
  clock.value = toHhmm(data.hhmm) || '21:30'
}

async function loadCalendar() {
  const data = await api.get('/api/hygiene/admin/deep-clean/calendar', {
    from_date: fromDate.value,
    to_date: toDate.value,
  })
  days.value = data.days || []
}

async function loadQueue() {
  const data = await api.get('/api/hygiene/admin/deep-clean/queue')
  queue.value = data.items || []
  if (selected.value) {
    const still = queue.value.find((row) => row.item_id === selected.value.item_id)
    if (still) {
      selected.value = still
      await loadReview(still)
    } else {
      selected.value = null
      review.value = null
    }
  }
}

async function saveClock() {
  if (savingClock.value) return
  savingClock.value = true
  clockHint.value = ''
  errorText.value = ''
  try {
    const data = await api.patch('/api/hygiene/admin/deep-clean/clock', {
      hhmm: toHhmm(clock.value),
    })
    clock.value = data.hhmm
    clockHint.value = '已保存。到点专项仍未全部验收会发企微群文字，漏做只记日历未完成。'
  } catch (err) {
    errorText.value = err.message || '无法保存专项逾期点'
  } finally {
    savingClock.value = false
  }
}

async function addItem() {
  const name = newName.value.trim()
  if (!name || adding.value) return
  adding.value = true
  errorText.value = ''
  try {
    await api.post('/api/hygiene/admin/deep-clean/items', {
      weekday: weekday.value,
      name,
    })
    newName.value = ''
    await loadItems()
    await loadCalendar()
  } catch (err) {
    errorText.value = err.message || '无法新增专项清单项'
  } finally {
    adding.value = false
  }
}

async function confirmRemoveItem() {
  const item = removeTarget.value
  removeTarget.value = null
  if (!item) return
  errorText.value = ''
  try {
    await api.delete(`/api/hygiene/admin/deep-clean/items/${item.id}`)
    await loadItems()
    await loadCalendar()
  } catch (err) {
    errorText.value = err.message || '无法删除专项清单项'
  }
}

async function loadReview(row) {
  review.value = await api.get(`/api/hygiene/admin/deep-clean/${row.item_id}/review`)
}

async function selectRow(row) {
  errorText.value = ''
  selected.value = row
  busy.value = true
  try {
    await loadReview(row)
  } catch (err) {
    review.value = null
    errorText.value = err.message || '无法打开前后对照'
  } finally {
    busy.value = false
  }
}

// 驳回不可撤销：专项回到"待回拍"，员工得重新拍前后对照。走一次确认。
const rejectOpen = ref(false)

function askReject() {
  if (!selected.value || busy.value) return
  rejectOpen.value = true
}


async function confirmReject(reason) {
  rejectOpen.value = false
  await decide('reject', reason)
}

async function decide(action, reason = '') {
  if (!selected.value || busy.value) return
  const current = selected.value
  const previous = queue.value
  busy.value = true
  errorText.value = ''
  try {
    await api.post(
      `/api/hygiene/admin/deep-clean/${selected.value.item_id}/${action}`,
      action === 'reject' && reason ? { reason } : undefined,
    )
    if (action === 'accept') {
      lastPassed.value = {
        item_id: selected.value.item_id,
        item_name: selected.value.item_name,
      }
      teachingHint.value = ''
    }
    selected.value = null
    review.value = null
    await loadQueue()
    await loadCalendar()
    const next = nextAfterRemove(previous, current, deepQueueKey)
    if (next) await selectRow(next)
  } catch (err) {
    errorText.value = err.message || (action === 'accept' ? '验收失败' : '驳回失败')
  } finally {
    busy.value = false
  }
}

async function markTeaching() {
  if (!lastPassed.value || busy.value) return
  busy.value = true
  errorText.value = ''
  teachingHint.value = ''
  try {
    await api.post('/api/hygiene/admin/teaching', {
      kind: 'deep_clean',
      item_id: lastPassed.value.item_id,
    })
    teachingHint.value = '已标成卫生教材，全员能打开。'
  } catch (err) {
    errorText.value = err.message || '无法标成卫生教材'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="deep-page">
    <header class="wbx-head">
      <div class="wbx-head-main">
        <p class="wbx-kicker">DEEP · 专项计划</p>
        <h1 class="wbx-title">专项卫生</h1>
      </div>
      <div class="wbx-pills">
        <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
      </div>
      <p class="wbx-sub">按星期几安排全店专项清洁，每项提交清理前和清理后。</p>
      <details class="rule-help">
        <summary>规则说明</summary>
        <p>专项不需要标准图。全部项目验收通过才算完成；漏做只在完成日历中记未完成，不进入红黑榜。</p>
      </details>
    </header>

    <p v-if="errorText" class="wbx-error" role="alert">{{ errorText }}</p>

    <div class="wbx-readline">
      <span class="wbx-read"><span>等验收</span><b>{{ queue.length }}</b></span>
      <span class="wbx-read"><span>{{ hygieneWeekdayLabel(weekday) }}清单</span><b>{{ weekdayItems.length }}</b></span>
      <span class="wbx-read"><span>未完成天数</span><b>{{ days.filter((d) => d.status === '未完成').length }}</b></span>
      <span class="wbx-read"><span>逾期点</span><b class="is-clock">{{ clock }}</b></span>
    </div>

    <!-- 逾期点：一条工具条（不再是第四块面板） -->
    <form class="wbx-toolbar" @submit.prevent="saveClock">
      <i class="led warn"></i>
      <span class="wbx-tool-label">专项逾期点</span>
      <label class="wbx-field" aria-label="专项卫生逾期点">
        <LuyunTimePicker v-model="clock" />
      </label>
      <button type="submit" class="btn btn-primary" :disabled="savingClock">保存</button>
      <span class="wbx-tool-hint">全店一个钟点 · 已交待验不算逾期</span>
      <p v-if="clockHint" class="wbx-tool-hint">{{ clockHint }}</p>
    </form>

    <div class="wbx-cols is-main">
      <section class="wbx-panel">
        <header class="wbx-ph">
          <i class="led sig"></i>
          <h2>专项清单</h2>
          <span class="wbx-hint">{{ weekdayItems.length }} 项</span>
        </header>
        <div class="weekday-tabs">
          <button
            v-for="(label, index) in HYGIENE_WEEKDAYS"
            :key="label"
            type="button"
            class="btn btn-sm"
            :class="{ 'btn-primary': weekday === index }"
            @click="weekday = index"
          >{{ label }}</button>
        </div>
        <p class="wbx-lead">{{ hygieneWeekdayLabel(weekday) }}全店一条，不需要标准图。</p>
        <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
        <div v-else-if="!weekdayItems.length" class="wbx-empty-bar">这一天还没有专项清单项。</div>
        <ul v-else class="item-list">
          <li v-for="item in weekdayItems" :key="item.id" class="item-row">
            <strong>{{ item.name }}</strong>
            <button type="button" class="btn btn-sm" @click="removeTarget = item">删除</button>
          </li>
        </ul>
        <form class="zone-add" @submit.prevent="addItem">
          <input
            v-model="newName"
            class="input"
            type="text"
            maxlength="40"
            placeholder="专项清单项名称，比如冷柜一号"
            aria-label="专项清单项名称"
          >
          <button type="submit" class="btn btn-primary" :disabled="adding || !newName.trim()">新增</button>
        </form>

        <!-- 第二段：完成日历（border-top 分隔，不再单独一块面板） -->
        <div class="wbx-seg">
          <header class="wbx-seg-head">
            <i class="led sig"></i>
            <h3>完成日历</h3>
            <span class="wbx-hint">{{ days.length }} 天</span>
          </header>
          <form class="cal-range" @submit.prevent="loadCalendar">
            <label class="wbx-field" aria-label="专项日历开始日期">
              <span>从</span>
              <LuyunDatePicker
                v-model="fromDate"
                dark
                placeholder="开始日期"
                aria-label="专项日历开始日期"
              />
            </label>
            <label class="wbx-field" aria-label="专项日历结束日期">
              <span>到</span>
              <LuyunDatePicker
                v-model="toDate"
                dark
                placeholder="结束日期"
                aria-label="专项日历结束日期"
              />
            </label>
            <button type="submit" class="btn">查看</button>
          </form>
          <div v-if="!days.length" class="wbx-empty-bar">这一段没有配专项清单的日子。</div>
          <ul v-else class="cal-list">
            <li v-for="day in days" :key="day.business_date" class="cal-row">
              <strong>{{ day.business_date }} · {{ day.weekday_name }}</strong>
              <span class="wbx-state" :class="day.status === '未完成' ? 'is-bad' : 'is-ok'">
                <i class="led" :class="day.status === '未完成' ? 'bad' : 'ok'"></i>{{ day.status }}
              </span>
            </li>
          </ul>
        </div>
      </section>

      <section class="wbx-panel">
        <header class="wbx-ph">
          <i class="led warn"></i>
          <h2>等验收</h2>
          <span class="wbx-hint">{{ queue.length }} 组</span>
        </header>
        <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
        <div v-else-if="!queue.length" class="wbx-empty-bar">现在没有等验收的专项前后对照。</div>
        <ul v-else class="queue-list">
          <li v-for="row in queue" :key="row.item_id">
            <button
              type="button"
              class="queue-btn"
              :class="{ active: selected && selected.item_id === row.item_id }"
              @click="selectRow(row)"
            >
              <strong>{{ row.item_name }}</strong>
              <span>{{ row.submitter_phone || '拍摄人未知' }}</span>
            </button>
          </li>
        </ul>

        <!-- 第二段：前后对照（同一个面板，border-top 分隔） -->
        <div class="wbx-seg is-review">
          <header class="wbx-seg-head">
            <i class="led ok"></i>
            <h3>{{ selected ? selected.item_name : '前后对照' }}</h3>
            <span class="wbx-hint">左清理前 · 右清理后</span>
          </header>
          <div v-if="!selected" class="wbx-empty-bar">点一组，对照清理前和清理后。</div>
          <div v-else-if="review" class="review-body">
            <p class="review-meta">拍摄人 {{ review.submitter_phone }} · 交这一组的人不能自己验</p>
            <HygieneReviewPair
              left-label="清理前"
              right-label="清理后"
              :standard-src="deepCleanShotUrl('admin', selected, 'before', 'preview')"
              :original-standard-src="deepCleanShotUrl('admin', selected, 'before')"
              :standard-alt="'清理前'"
              :left-watermark="review.before_watermark"
              :capture-src="deepCleanShotUrl('admin', selected, 'after', 'preview')"
              :original-capture-src="deepCleanShotUrl('admin', selected, 'after')"
              :capture-alt="'清理后'"
              :watermark="review.after_watermark || review.watermark"
            />
            <div class="review-actions">
              <button type="button" class="btn btn-primary" :disabled="busy" @click="decide('accept')">通过</button>
              <button type="button" class="btn btn-danger" :disabled="busy" @click="askReject">驳回</button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <p v-if="lastPassed" class="wbx-hintline">
      刚通过：{{ lastPassed.item_name }}。
      <button type="button" class="btn btn-sm" :disabled="busy" @click="markTeaching">标为卫生教材</button>
      <span v-if="teachingHint">{{ teachingHint }}</span>
    </p>

    <ConfirmDialog
      v-if="rejectOpen"
      title="驳回这次前后对照"
      :message="`驳回「${selected ? selected.item_name : ''}」后要重新拍清理前后；本周红黑榜会记一次驳回。`"
      confirm-label="驳回"
      danger
      :prompt="{ label: '哪里不合格（必填，员工能看到）', placeholder: '写一句让他知道改什么，例如：滤网没拆下来洗', hint: '必填 · 员工照这句重做', required: true, maxlength: 120 }"
      @confirm="confirmReject"
      @cancel="rejectOpen = false"
    />

    <ConfirmDialog
      v-if="removeTarget"
      title="删除专项清单项"
      :message="`从${hygieneWeekdayLabel(removeTarget.weekday)}清单删除「${removeTarget.name}」？`"
      confirm-label="删除"
      danger
      @confirm="confirmRemoveItem"
      @cancel="removeTarget = null"
    />
  </div>
</template>

<style scoped>
/* B 案 v2 + v3 布局纪律：页头右手只留刷新；一条读数行；逾期点降为工具条；
   两块面板各两段（清单/日历、等验收/前后对照）；空态一律横条。 */
.deep-page {
  display: flex; flex-direction: column; gap: 14px;
  padding-bottom: calc(40px + var(--safe-b));
}
.wbx-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 16px; align-items: end; }
.wbx-head-main { grid-column: 1; grid-row: 1; }
.wbx-kicker { margin: 0 0 7px; font: 500 9.5px/1 var(--font-sans); letter-spacing: .26em; color: var(--ink-faint); }
.wbx-title { margin: 0; font: 600 26px/1.1 var(--font-sans); letter-spacing: .01em; color: var(--ink); }
.wbx-pills { grid-column: 2; grid-row: 1; justify-self: end; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wbx-sub { grid-column: 1 / -1; grid-row: 2; margin: 8px 0 0; font-size: 11.5px; line-height: 1.6; color: var(--ink-dim); }
.wbx-head .rule-help { grid-column: 1 / -1; grid-row: 3; margin-top: 6px; }
.wbx-error { margin: 0; font-size: 11px; color: var(--danger-bright); }
.led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.led.ok { background: var(--ok); }
.led.warn { background: var(--warn); }
.led.bad { background: var(--danger); }
.led.sig { background: var(--action); }

.wbx-readline {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 26px;
  padding: 10px 14px;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08);
}
.wbx-read { display: inline-flex; align-items: baseline; gap: 6px; }
.wbx-read span { font: 500 9.5px/1 var(--font-sans); letter-spacing: .18em; color: var(--ink-faint); }
.wbx-read b { font: 500 20px/1 var(--font-mono); color: var(--ink); }
.wbx-read b.is-clock { font-size: 16px; }

.wbx-toolbar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  padding: 8px 14px;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08);
}
.wbx-tool-label { font: 600 10px/1 var(--font-sans); letter-spacing: .12em; color: var(--ink); }
.wbx-tool-hint { margin: 0; font: 500 9.5px/1.4 var(--font-mono); color: var(--ink-faint); }
.wbx-field { display: flex; flex-direction: column; gap: 5px; font-size: 10px; color: var(--ink-dim); }
.wbx-field :deep(.luyun-time-picker) { width: 100px; }
.wbx-field :deep(.luyun-date-picker) { width: 150px; }
.wbx-hintline { margin: 0; font-size: 10.5px; color: var(--ink-faint); }

.wbx-cols { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 14px; align-items: stretch; }
.wbx-panel {
  display: flex; flex-direction: column;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08); overflow: hidden;
}
.wbx-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbx-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbx-hint { margin-left: auto; font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbx-lead { margin: 0; padding: 8px 14px 0; font-size: 10px; line-height: 1.6; color: var(--ink-faint); }
.wbx-empty-bar {
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; margin: 0; padding: 10px 14px;
  font-size: 10.5px; color: var(--ink-faint);
}
.wbx-seg { border-top: 1px solid var(--wb-ink-a08); }
.wbx-seg-head { display: flex; align-items: center; gap: 9px; padding: 10px 14px; }
.wbx-seg-head h3 { margin: 0; font: 600 11.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }

.weekday-tabs { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 14px 0; }
.item-list { list-style: none; margin: 0; padding: 4px 14px; display: flex; flex-direction: column; }
.item-row {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  min-height: 56px; padding: 8px 0; border-bottom: 1px solid var(--wb-ink-a08);
}
.item-row:last-child { border-bottom: 0; }
.item-row strong { font-size: 11.5px; font-weight: 600; color: var(--ink); }
.zone-add { display: flex; gap: 8px; padding: 10px 14px; margin-top: auto; border-top: 1px solid var(--wb-ink-a08); }
.zone-add .input { flex: 1; min-width: 0; }
.cal-range { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px; padding: 0 14px 10px; }
.cal-list { list-style: none; margin: 0; padding: 0 14px 12px; display: flex; flex-direction: column; }
.cal-row {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px;
  min-height: 48px; padding: 6px 0; border-bottom: 1px solid var(--wb-ink-a08);
}
.cal-row:last-child { border-bottom: 0; }
.cal-row strong { font: 500 10.5px/1.5 var(--font-mono); color: var(--ink); }
.wbx-state { display: inline-flex; align-items: center; gap: 5px; font-size: 9px; letter-spacing: .06em; color: var(--ink-dim); }
.wbx-state.is-bad { color: var(--danger-bright); }
.wbx-state.is-ok { color: var(--ok); }

.queue-list { list-style: none; margin: 0; padding: 0; }
.queue-btn {
  display: flex; flex-direction: column; align-items: flex-start; gap: 3px;
  width: 100%; min-height: 56px; padding: 8px 14px; text-align: left; cursor: pointer;
  background: transparent; border: 0; border-bottom: 1px solid var(--wb-ink-a08); color: var(--ink);
}
.queue-btn:hover { background: var(--surface-2); }
.queue-btn strong { font-size: 11.5px; font-weight: 600; }
.queue-btn span { font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.queue-btn.active { background: var(--surface-2); box-shadow: inset 3px 0 0 var(--action); }
.review-body { padding: 10px 14px 14px; display: flex; flex-direction: column; gap: 10px; }
.review-meta { margin: 0; font: 500 10px/1.5 var(--font-mono); color: var(--ink-faint); }
.review-actions { display: flex; justify-content: flex-end; gap: 8px; }
.deep-page :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }
@media (max-width: 900px) {
  .wbx-cols { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .wbx-head { grid-template-columns: minmax(0, 1fr); }
  .wbx-pills { grid-column: 1; grid-row: 2; justify-self: start; margin-top: 10px; }
  .wbx-sub { grid-row: 3; }
  .wbx-head .rule-help { grid-row: 4; }
  .wbx-title { font-size: 22px; }
}
</style>
