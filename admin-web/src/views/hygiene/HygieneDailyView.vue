<script setup>
import { onMounted, ref } from 'vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { dailyCaptureUrl, frozenStandardUrl } from '../../utils/hygieneMarkup'
import { dailyQueueKey, nextAfterRemove } from '../../utils/hygieneWorkFlow'

const items = ref([])
const loading = ref(true)
const errorText = ref('')
const busy = ref(false)
const selected = ref(null)
const review = ref(null)
const lastPassed = ref(null)
const teachingHint = ref('')

onMounted(loadQueue)

useHygieneRealtime({
  id: 'hygiene-admin-daily',
  resources: ['daily'],
  pull: loadQueue,
})

async function loadQueue() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/daily-queue')
    items.value = data.items || []
    if (selected.value) {
      const still = items.value.find(
        (row) => row.item_id === selected.value.item_id && row.shift === selected.value.shift,
      )
      if (still) {
        selected.value = still
        await loadReview(still)
      } else {
        selected.value = null
        review.value = null
      }
    }
  } catch (err) {
    errorText.value = err.message || '无法加载待验收日常'
  } finally {
    loading.value = false
  }
}

async function loadReview(row) {
  review.value = await api.get(`/api/hygiene/admin/daily/${row.item_id}/review`, {
    shift: row.shift,
  })
}

async function selectRow(row) {
  errorText.value = ''
  selected.value = row
  busy.value = true
  try {
    await loadReview(row)
  } catch (err) {
    review.value = null
    errorText.value = err.message || '无法打开对照'
  } finally {
    busy.value = false
  }
}

async function decide(action) {
  if (!selected.value || busy.value) return
  const current = selected.value
  const previous = items.value
  busy.value = true
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/daily/${selected.value.item_id}/${action}`, {
      shift: selected.value.shift,
    })
    if (action === 'accept') {
      lastPassed.value = {
        item_id: selected.value.item_id,
        shift: selected.value.shift,
        item_name: selected.value.item_name,
        zone_name: selected.value.zone_name,
      }
      teachingHint.value = ''
    }
    selected.value = null
    review.value = null
    await loadQueue()
    const next = nextAfterRemove(previous, current, dailyQueueKey)
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
      kind: 'daily',
      item_id: lastPassed.value.item_id,
      shift: lastPassed.value.shift,
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
  <div class="daily-page">
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Daily · 当日对照</p>
        <h1>日常验收</h1>
        <p>对照提交当时的标准图验收员工实拍。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>左标准图、右实拍。提交人不能验收自己的实拍，超级管理员可在这里验收。</p>
        </details>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="loadQueue">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>
    <div v-if="lastPassed" class="card teach-banner">
      <p>刚通过：{{ lastPassed.zone_name }} · {{ lastPassed.item_name }} · {{ lastPassed.shift }}。</p>
      <button type="button" class="btn btn-primary" :disabled="busy" @click="markTeaching">标为卫生教材</button>
      <p v-if="teachingHint" class="clocks-hint">{{ teachingHint }}</p>
    </div>

    <div class="daily-grid">
      <div class="table-card">
        <div class="table-card-header">
          <h3>待验收 <span>{{ items.length }}</span></h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="!items.length" class="roster-empty">现在没有待验收的日常检查。</div>
        <ul v-else class="queue-list">
          <li v-for="row in items" :key="`${row.item_id}-${row.shift}`">
            <button
              type="button"
              class="queue-btn"
              :class="{ active: selected && selected.item_id === row.item_id && selected.shift === row.shift }"
              @click="selectRow(row)"
            >
              <strong>{{ row.zone_name }} · {{ row.item_name }}</strong>
              <span>{{ row.shift }} · {{ row.submitter_phone || '拍摄人未知' }}</span>
            </button>
          </li>
        </ul>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>{{ selected ? selected.item_name : '对照验收' }}</h3>
        </div>
        <div v-if="!selected" class="roster-empty">点一项，对照提交当时的标准图。</div>
        <div v-else-if="review" class="review-body">
          <p class="review-meta">{{ selected.zone_name }} · {{ selected.shift }} · 拍摄人 {{ review.submitter_phone }}</p>
          <HygieneReviewPair
            :standard-src="frozenStandardUrl('admin', selected, 'preview')"
            :original-standard-src="frozenStandardUrl('admin', selected)"
            :standard-markup="review.frozen_markup || []"
            :standard-alt="selected.item_name"
            :capture-src="dailyCaptureUrl('admin', selected, 'preview')"
            :original-capture-src="dailyCaptureUrl('admin', selected)"
            :capture-alt="'实拍'"
            :watermark="review.watermark"
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
.daily-grid {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 14px;
  align-items: start;
}
@media (max-width: 900px) {
  .daily-grid { grid-template-columns: 1fr; }
}
.queue-list {
  list-style: none;
  margin: 0;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.review-body { padding: 14px; display: flex; flex-direction: column; gap: 14px; }
.review-meta {
  margin: 0;
  color: var(--hy-muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.review-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.teach-banner p { max-width: 60em; }
</style>
