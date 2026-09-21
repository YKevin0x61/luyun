<script setup>
import { computed, onMounted, ref } from 'vue'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { dailyCaptureUrl, frozenStandardUrl } from '../../utils/hygieneMarkup'
import { dailyQueueKey, nextAfterRemove } from '../../utils/hygieneWorkFlow'

const rows = ref([])
const loading = ref(true)
const errorText = ref('')
const busy = ref(false)
const selected = ref(null)
const review = ref(null)
const reviewMissing = ref(false)
const lastPassed = ref(null)
const teachingHint = ref('')

// 空串 = 今天。服务端认这个口径：不带 date 时就是当前营业日，只回待验收。
const businessDate = ref('')
// 服务端回的营业日与"是不是今天"，历史回看只读就靠 is_today（ADR-0088）。
const queueDate = ref('')
const isToday = ref(true)

const pendingRows = computed(() => rows.value.filter((row) => row.status === '待验收'))
const listRows = computed(() => (isToday.value ? pendingRows.value : rows.value))
const reviewDate = computed(() => businessDate.value || '')

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
    const data = await api.get('/api/hygiene/admin/daily-queue', {
      date: businessDate.value || undefined,
    })
    rows.value = data.items || []
    queueDate.value = data.date || ''
    isToday.value = data.is_today !== false
    if (selected.value) {
      const still = rows.value.find(
        (row) => row.item_id === selected.value.item_id && row.shift === selected.value.shift,
      )
      if (still) {
        selected.value = still
        await loadReview(still)
      } else {
        selected.value = null
        review.value = null
        reviewMissing.value = false
      }
    }
  } catch (err) {
    errorText.value = err.message || '无法加载日常检查'
  } finally {
    loading.value = false
  }
}

function goToday() {
  if (isToday.value && !businessDate.value) return
  businessDate.value = ''
  return loadQueue()
}

async function loadReview(row) {
  reviewMissing.value = false
  try {
    review.value = await api.get(`/api/hygiene/admin/daily/${row.item_id}/review`, {
      shift: row.shift,
      date: reviewDate.value || undefined,
    })
  } catch (err) {
    review.value = null
    // 404 是"那天这一项没交过（或照片已被清理）"，不是故障；其余照样报错。
    if (err.status !== 404) throw err
    reviewMissing.value = true
  }
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

// 驳回是不可撤销的：状态直接回到"待拍"，员工得重拍。按钮就挨着"通过"，
// 手机上很容易误触，所以走一次确认。
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
  const previous = [...listRows.value]
  busy.value = true
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/daily/${selected.value.item_id}/${action}`, {
      shift: selected.value.shift,
      // 只在驳回时带上原因：员工端会把它显示在待办行上。
      ...(action === 'reject' && reason ? { reason } : {}),
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
    reviewMissing.value = false
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

function statusClass(status) {
  if (status === '待验收') return 'pending'
  if (status === '已通过') return 'passed'
  return 'todo'
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
          <p>切到过去的营业日只是回看：能看到那天交了什么、漏了哪些，不能补验收。</p>
        </details>
      </div>
      <div class="daily-datebar">
        <LuyunDatePicker
          v-model="businessDate"
          dark
          placeholder="选择营业日"
          aria-label="日常验收营业日"
          @update:model-value="loadQueue"
        />
        <button type="button" class="btn" :disabled="loading || (isToday && !businessDate)" @click="goToday">
          今天
        </button>
        <button type="button" class="btn" :disabled="loading" @click="loadQueue">刷新</button>
      </div>
    </div>

    <p v-if="!isToday" class="daily-history-hint" role="status">
      正在回看 {{ queueDate }}：这一天只读，不能通过或驳回。
    </p>
    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>
    <div v-if="lastPassed && isToday" class="card teach-banner">
      <p>刚通过：{{ lastPassed.zone_name }} · {{ lastPassed.item_name }} · {{ lastPassed.shift }}。</p>
      <button type="button" class="btn btn-primary" :disabled="busy" @click="markTeaching">标为卫生教材</button>
      <p v-if="teachingHint" class="clocks-hint">{{ teachingHint }}</p>
    </div>

    <div class="daily-grid">
      <div class="table-card">
        <div class="table-card-header">
          <h3>
            {{ isToday ? '待验收' : `${queueDate} 全部检查项` }}
            <span>{{ listRows.length }}</span>
          </h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="errorText" class="roster-empty">加载失败，点上方「刷新」重试。</div>
        <div v-else-if="!listRows.length" class="roster-empty">
          {{ isToday ? '现在没有待验收的日常检查。' : '这一天没有配日常检查项。' }}
        </div>
        <ul v-else class="queue-list">
          <li v-for="row in listRows" :key="`${row.item_id}-${row.shift}`">
            <button
              type="button"
              class="queue-btn"
              :class="{ active: selected && selected.item_id === row.item_id && selected.shift === row.shift }"
              @click="selectRow(row)"
            >
              <strong>{{ row.zone_name }} · {{ row.item_name }}</strong>
              <span>
                {{ row.shift }} · {{ row.submitter_phone || '拍摄人未知' }}
                <em v-if="!isToday" class="queue-status" :class="statusClass(row.status)">{{ row.status }}</em>
              </span>
            </button>
          </li>
        </ul>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>{{ selected ? selected.item_name : '对照验收' }}</h3>
        </div>
        <div v-if="!selected" class="roster-empty">点一项，对照提交当时的标准图。</div>
        <div v-else-if="reviewMissing" class="roster-empty">
          「{{ selected.item_name }}」在这一天没有照片（未提交，或已经被清理）。
        </div>
        <div v-else-if="review" class="review-body">
          <p class="review-meta">
            {{ selected.zone_name }} · {{ selected.shift }} · 拍摄人 {{ review.submitter_phone }}
            <template v-if="!isToday"> · 状态 {{ review.status }}</template>
          </p>
          <HygieneReviewPair
            :standard-src="review.standard_available ? frozenStandardUrl('admin', selected, 'preview', reviewDate) : ''"
            :original-standard-src="review.standard_available ? frozenStandardUrl('admin', selected, 'original', reviewDate) : ''"
            :standard-markup="review.frozen_markup || []"
            :standard-alt="selected.item_name"
            :capture-src="review.capture_available ? dailyCaptureUrl('admin', selected, 'preview', reviewDate) : ''"
            :original-capture-src="review.capture_available ? dailyCaptureUrl('admin', selected, 'original', reviewDate) : ''"
            :capture-alt="'实拍'"
            :watermark="review.watermark"
          />
          <p v-if="!review.standard_available || !review.capture_available" class="review-note">
            这张照片或当时的标准图已经被「数据与照片」清理掉了。
          </p>
          <div v-if="isToday && selected.status === '待验收'" class="review-actions">
            <button type="button" class="btn btn-primary" :disabled="busy" @click="decide('accept')">通过</button>
            <button type="button" class="btn btn-danger" :disabled="busy" @click="askReject">驳回</button>
          </div>
          <p v-else-if="!isToday" class="review-note">历史回看：这一天不能补验收。</p>
        </div>
      </div>
    </div>

    <ConfirmDialog
      v-if="rejectOpen"
      title="驳回这一项"
      :message="`驳回「${selected ? selected.item_name : ''}」后状态回到待拍，员工要重新拍；本周红黑榜会记一次驳回。`"
      confirm-label="驳回"
      danger
      :prompt="{ label: '哪里不合格（可选，员工能看到）', placeholder: '例如：台面还有油渍、角落没擦到', maxlength: 120 }"
      @confirm="confirmReject"
      @cancel="rejectOpen = false"
    />
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
.daily-datebar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.daily-history-hint {
  margin: 0 0 10px;
  padding: 8px 12px;
  border-left: 3px solid var(--hy-mint, #3fe0b0);
  background: rgba(63, 224, 176, .08);
  color: var(--hy-muted);
  font-size: 13px;
}
.queue-list {
  list-style: none;
  margin: 0;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.queue-status {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 11px;
  font-style: normal;
  border: 1px solid var(--hy-line, rgba(133, 205, 198, .28));
}
.queue-status.pending { color: #ffd479; border-color: rgba(255, 212, 121, .5); }
.queue-status.passed { color: #3fe0b0; border-color: rgba(63, 224, 176, .5); }
.queue-status.todo { color: var(--hy-muted); }
.review-body { padding: 14px; display: flex; flex-direction: column; gap: 14px; }
.review-meta {
  margin: 0;
  color: var(--hy-muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.review-note { margin: 0; color: var(--hy-muted); font-size: 13px; }
.review-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.teach-banner p { max-width: 60em; }
</style>
