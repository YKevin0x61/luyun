<script setup>
import { computed, onMounted, ref } from 'vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import { api } from '../../api/client'
import { HYGIENE_WEEKDAYS, hygieneWeekdayLabel } from '../../utils/hygieneCopy'
import { deepCleanShotUrl } from '../../utils/hygieneMarkup'

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
    errorText.value = err.message || '无法上架专项清单项'
  } finally {
    adding.value = false
  }
}

async function removeItem(item) {
  if (!window.confirm(`从${hygieneWeekdayLabel(item.weekday)}清单去掉「${item.name}」？`)) return
  errorText.value = ''
  try {
    await api.delete(`/api/hygiene/admin/deep-clean/items/${item.id}`)
    await loadItems()
    await loadCalendar()
  } catch (err) {
    errorText.value = err.message || '无法去掉专项清单项'
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

async function decide(action) {
  if (!selected.value || busy.value) return
  busy.value = true
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/deep-clean/${selected.value.item_id}/${action}`)
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
    <div class="card roster-head">
      <div>
        <h2>专项卫生</h2>
        <p>按星期几配全店一条。不要标准图。每项拍清理前和清理后，全部验收才算完成。漏做只在日历记未完成，不上人的红黑榜，也不红卫生责任区。</p>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>
    <div v-if="lastPassed" class="card teach-banner">
      <p>刚通过：{{ lastPassed.item_name }}。合格图不会自动进教材。</p>
      <button type="button" class="btn btn-primary" :disabled="busy" @click="markTeaching">标为卫生教材</button>
      <p v-if="teachingHint" class="clocks-hint">{{ teachingHint }}</p>
    </div>

    <form class="card clocks-card" @submit.prevent="saveClock">
      <div>
        <h3>专项逾期点</h3>
        <p>全店一个钟点。到点这条专项还有未验收的组，就发一次企微群文字。</p>
      </div>
      <label class="clock-field">
        专项
        <input
          v-model="clock"
          class="input"
          type="time"
          required
          aria-label="专项卫生逾期点"
        >
      </label>
      <button type="submit" class="btn btn-primary" :disabled="savingClock">保存逾期点</button>
      <p v-if="clockHint" class="clocks-hint">{{ clockHint }}</p>
    </form>

    <div class="deep-grid">
      <div class="table-card">
        <div class="table-card-header">
          <h3>专项清单</h3>
        </div>
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
        <p class="editor-lead">{{ hygieneWeekdayLabel(weekday) }}全店一条。上架专项清单项不需要标准图。</p>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="!weekdayItems.length" class="roster-empty">这一天还没有专项清单项。</div>
        <ul v-else class="item-list">
          <li v-for="item in weekdayItems" :key="item.id" class="item-row">
            <strong>{{ item.name }}</strong>
            <button type="button" class="btn btn-sm" @click="removeItem(item)">去掉</button>
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
          <button type="submit" class="btn btn-primary" :disabled="adding || !newName.trim()">上架</button>
        </form>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>完成日历</h3>
        </div>
        <form class="cal-range" @submit.prevent="loadCalendar">
          <label class="clock-field">
            从
            <input v-model="fromDate" class="input" type="date" required aria-label="专项日历开始日期">
          </label>
          <label class="clock-field">
            到
            <input v-model="toDate" class="input" type="date" required aria-label="专项日历结束日期">
          </label>
          <button type="submit" class="btn">查看</button>
        </form>
        <div v-if="!days.length" class="roster-empty">这一段没有配专项清单的日子。</div>
        <ul v-else class="cal-list">
          <li v-for="day in days" :key="day.business_date" class="cal-row">
            <strong>{{ day.business_date }} · {{ day.weekday_name }}</strong>
            <span :class="{ missed: day.status === '未完成', done: day.status === '已完成' }">{{ day.status }}</span>
          </li>
        </ul>
      </div>
    </div>

    <div class="daily-grid">
      <div class="table-card">
        <div class="table-card-header">
          <h3>待验收 <span>{{ queue.length }}</span></h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="!queue.length" class="roster-empty">现在没有待验收的专项前后对照。</div>
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
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>{{ selected ? selected.item_name : '前后对照' }}</h3>
        </div>
        <div v-if="!selected" class="roster-empty">从左边点一组，对照清理前和清理后。</div>
        <div v-else-if="review" class="review-body">
          <p class="review-meta">拍摄人 {{ review.submitter_phone }} · 交这一组的人不能自己验</p>
          <HygieneReviewPair
            left-label="清理前"
            right-label="清理后"
            :standard-src="deepCleanShotUrl('admin', selected, 'before')"
            :standard-alt="'清理前'"
            :left-watermark="review.before_watermark"
            :capture-src="deepCleanShotUrl('admin', selected, 'after')"
            :capture-alt="'清理后'"
            :watermark="review.after_watermark || review.watermark"
          />
          <div class="review-actions">
            <button type="button" class="btn btn-primary" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-danger" :disabled="busy" @click="decide('reject')">驳回</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.deep-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 1180px;
}
.roster-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
}
.roster-head h2 { margin: 0 0 6px; font-size: 16px; }
.roster-head p {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
  max-width: 56em;
}
.clocks-card {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px 16px;
  padding: 14px 16px;
}
.clocks-card h3 { margin: 0 0 4px; font-size: 14px; }
.clocks-card p {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.55;
  max-width: 42em;
}
.clock-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
}
.clock-field .input { min-width: 140px; }
.clocks-hint {
  flex: 1 1 100%;
  margin: 0;
  color: var(--cyan);
  font-size: 12px;
}
.roster-error {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 13px;
}
.roster-empty {
  padding: 28px 16px;
  text-align: center;
  color: var(--text-dim);
  font-size: 13px;
}
.deep-grid,
.daily-grid {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 12px;
  align-items: start;
}
@media (max-width: 900px) {
  .deep-grid,
  .daily-grid { grid-template-columns: 1fr; }
}
.weekday-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 8px 0;
}
.editor-lead {
  margin: 0;
  padding: 8px 12px 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.55;
}
.item-list {
  list-style: none;
  margin: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.item-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card2);
}
.zone-add {
  display: flex;
  gap: 8px;
  padding: 8px 8px 12px;
}
.zone-add .input { flex: 1; }
.cal-range {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  padding: 12px;
}
.cal-list {
  list-style: none;
  margin: 0;
  padding: 8px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cal-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card2);
  font-size: 13px;
}
.cal-row .missed { color: #fca5a5; }
.cal-row .done { color: #6ee7b7; }
.queue-list {
  list-style: none;
  margin: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.queue-btn {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  background: var(--card2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  font-family: inherit;
}
.queue-btn span { color: var(--text-dim); font-size: 12px; }
.queue-btn.active { border-color: var(--accent); }
.review-body { padding: 12px; display: flex; flex-direction: column; gap: 12px; }
.review-meta { margin: 0; color: var(--text-dim); font-size: 13px; }
.review-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.teach-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
}
.teach-banner p { margin: 0; color: var(--text-dim); font-size: 13px; }

</style>
