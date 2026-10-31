<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { dailyCaptureUrl, frozenStandardUrl } from '../../utils/hygieneMarkup'
import { dayLabel as businessDayLabel } from '../../utils/todayShift'
import { dailyQueueKey, nextAfterRemove } from '../../utils/hygieneWorkFlow'

const rows = ref([])
// 隔夜验收（2026-10-18）：过去几个营业日里还挂着「待验收」的行。服务端**只在
// 不带 date 的今天这一档**回 `pending_past`，每行带 `business_date`。
const pendingPast = ref([])
const loading = ref(true)
const errorText = ref('')
const busy = ref(false)
const selected = ref(null)
const review = ref(null)
const reviewMissing = ref(false)
const lastPassed = ref(null)
const teachingHint = ref('')
// 「通过」是可撤销的（2026-10-18）：验完留一条回执，10 秒后自动消失。
// 回执放在一条**固定高度**的提示条里，连批时不会改变下方布局 —— 通过/驳回按钮
// 因此不会在指下漂移（原来那条「刚通过…标为卫生教材」横幅会把对照区往下推）。
const undoOffer = ref(null)
const undoError = ref('')
const undoNotice = ref('')
const reviewError = ref('')
let undoTimer = null
let noticeTimer = null
const UNDO_TTL_MS = 10_000

// 空串 = 今天。服务端认这个口径：不带 date 时就是当前营业日，只回等验收的。
const businessDate = ref('')
// 服务端回的营业日与"是不是今天"，历史回看只读就靠 is_today（ADR-0088）。
const queueDate = ref('')
const isToday = ref(true)

const pendingRows = computed(() => rows.value.filter((row) => row.status === '待验收'))
const listRows = computed(() => (isToday.value ? pendingRows.value : rows.value))
// 只有「当前待验收」的行能操作：今天这一档的 `items` 与漏验都在内，
// 历史回看（手动选了过去的营业日）那份只读。
const pastPendingRows = computed(() => pendingPast.value.filter((row) => row.status === '待验收'))
const operableRows = computed(() => (
  isToday.value ? [...listRows.value, ...pastPendingRows.value] : []
))
// 漏验按营业日分组的显示标题：『10/17 周六』（日期倒序由服务端给，这里只做分组）。
const pastGroups = computed(() => {
  const groups = []
  for (const row of pastPendingRows.value) {
    const date = row.business_date || ''
    const found = groups.find((group) => group.date === date)
    if (found) {
      found.rows.push(row)
      continue
    }
    groups.push({ date, title: businessDayLabel(date) || date, rows: [row] })
  }
  return groups
})
// 对照图与验收请求要的那一天：漏验行跟着它自己的 `business_date`，其余按当前回看日
// （今天这一档不带日期，与旧口径逐字一致 —— 服务端不传 date 就是今天）。
const reviewDate = computed(() => {
  const row = selected.value
  const rowDate = (row && row.business_date) || ''
  if (rowDate && rowDate !== queueDate.value) return rowDate
  return businessDate.value || ''
})
const emptyText = computed(() => {
  if (!isToday.value) return '这一天没有配日常检查项。'
  if (pastPendingRows.value.length) return '今天没有待验收的日常检查。'
  return '今天没有待验收的日常检查，也没有漏验。'
})
// 「验完这一件之后选下一件」的键要带上营业日：同一项同班次在漏验那几天各是一条。
function queueKeyOf(row) {
  return `${dailyQueueKey(row)}@${(row && row.business_date) || ''}`
}

function clearUndoTimer() {
  if (undoTimer) window.clearTimeout(undoTimer)
  undoTimer = null
}

function clearNoticeTimer() {
  if (noticeTimer) window.clearTimeout(noticeTimer)
  noticeTimer = null
}

/** 一次「通过」之后留一条可撤销的回执（同一项同班次同营业日才算同一条）。 */
function offerUndo(row, targetDate) {
  const offer = {
    item_id: row.item_id,
    shift: row.shift,
    business_date: targetDate || '',
    item_name: row.item_name,
  }
  undoOffer.value = offer
  undoError.value = ''
  undoNotice.value = ''
  clearUndoTimer()
  undoTimer = window.setTimeout(() => {
    undoTimer = null
    const current = undoOffer.value
    // 10 秒里已经验了下一项的话，回执被换过 —— 别把新的收掉。
    if (
      current
      && current.item_id === offer.item_id
      && current.shift === offer.shift
      && current.business_date === offer.business_date
    ) {
      undoOffer.value = null
    }
  }, UNDO_TTL_MS)
}

/** 一句状态话（「已撤销，回到待验收」这类），同样 10 秒后自己收掉。 */
function setNotice(text) {
  undoNotice.value = text
  clearNoticeTimer()
  noticeTimer = window.setTimeout(() => {
    noticeTimer = null
    undoNotice.value = ''
  }, UNDO_TTL_MS)
}

onMounted(loadQueue)

onBeforeUnmount(() => {
  clearUndoTimer()
  clearNoticeTimer()
})

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
    pendingPast.value = data.pending_past || []
    queueDate.value = data.date || ''
    isToday.value = data.is_today !== false
    if (selected.value) {
      const current = selected.value
      const still = [...rows.value, ...pendingPast.value].find(
        (row) => row.item_id === current.item_id
          && row.shift === current.shift
          && (row.business_date || '') === (current.business_date || ''),
      )
      if (still) {
        selected.value = still
        await openReviewPanel(still)
      } else {
        selected.value = null
        review.value = null
        reviewMissing.value = false
        reviewError.value = ''
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
    await openReviewPanel(row)
  } finally {
    busy.value = false
  }
}

/** 打开对照：404 是「那天没交照片」（面板里说清楚），其余失败落到面板内的错误态 ——
 *  不能只写页面顶上那条 alert，否则右侧整块面板三个分支都不渲染、看起来像卡死了。 */
async function openReviewPanel(row) {
  reviewError.value = ''
  try {
    await loadReview(row)
  } catch (err) {
    review.value = null
    reviewError.value = err.message || '无法打开对照'
  }
}

async function retryReview() {
  if (!selected.value || busy.value) return
  busy.value = true
  try {
    await openReviewPanel(selected.value)
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
  const previous = [...operableRows.value]
  const targetDate = reviewDate.value
  busy.value = true
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/daily/${selected.value.item_id}/${action}`, {
      shift: selected.value.shift,
      // 漏验要补在它自己那一天；今天这一档不传 = 今天（与旧行为一致）。
      ...(targetDate ? { business_date: targetDate } : {}),
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
      // 通过后这个项就能撤：回执里带着它的 item_id / shift / 营业日。
      offerUndo(current, targetDate)
    }
    selected.value = null
    review.value = null
    reviewMissing.value = false
    reviewError.value = ''
    await loadQueue()
    const next = nextAfterRemove(previous, current, queueKeyOf)
    if (next) await selectRow(next)
  } catch (err) {
    errorText.value = err.message || (action === 'accept' ? '验收失败' : '驳回失败')
  } finally {
    busy.value = false
  }
}

/** 撤销刚才这次「通过」：成功回执收掉、队列重拉；失败保留回执并说出原因，人可以再点一次。 */
async function undoAccept() {
  if (!undoOffer.value || busy.value) return
  const offer = undoOffer.value
  busy.value = true
  undoError.value = ''
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/daily/${offer.item_id}/undo-accept`, {
      shift: offer.shift,
      // 漏验要撤它自己那一天；今天这一档不传 = 今天（与通过/驳回同一口径）。
      ...(offer.business_date ? { business_date: offer.business_date } : {}),
    })
    undoOffer.value = null
    clearUndoTimer()
    // 撤销之后「刚通过」不再成立，标教材那条回执跟着收掉。
    lastPassed.value = null
    teachingHint.value = ''
    setNotice('已撤销，回到待验收')
    await loadQueue()
  } catch (err) {
    undoError.value = err.message || '撤销失败'
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
    <header class="roster-head">
      <div>
        <p class="hy-eyebrow">Daily · 当日对照</p>
        <h1>日常验收</h1>
        <p>对照提交当时的标准图验收员工实拍。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>左标准图、右实拍。提交人不能验收自己的实拍，超级管理员可在这里验收。</p>
          <p>切到过去的营业日只是回看：能看到那天交了什么、漏了哪些，不能补验收。</p>
          <p>漏验的（最近几个营业日还没验的）在「今天等验收」下面按天列出，可以补验。</p>
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
    </header>

    <p v-if="!isToday" class="daily-history-hint" role="status">
      正在回看 {{ queueDate }}：这一天只读，不能通过或驳回。
    </p>
    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>
    <!-- 通过回执 + 标教材：一条**固定高度**的提示行（今天这一档常驻，空着也占同一行高）。
         通过/驳回只换内容、不改高度 —— 连批时下方的通过/驳回按钮不会因此在指下漂移；
         原来那条横幅插在对照区上方，每验一项都把按钮往下推。撤销回执 10 秒后自动收掉，
         「刚通过 → 标为卫生教材」留到下一次验收。 -->
    <div v-if="isToday" class="daily-receipt" aria-live="polite">
      <span v-if="undoOffer" class="daily-receipt-item">
        <b class="daily-receipt-verb">{{ undoError ? '撤销失败' : '已通过' }}</b>
        <span v-if="undoError" class="daily-receipt-reason" role="alert">{{ undoError }}</span>
        <span v-else class="daily-receipt-text">{{ undoOffer.item_name }}</span>
        <button type="button" class="btn daily-receipt-btn" :disabled="busy" @click="undoAccept">撤销</button>
      </span>
      <span v-else-if="undoNotice" class="daily-receipt-item">
        <b class="daily-receipt-verb is-ok">{{ undoNotice }}</b>
      </span>
      <span v-if="lastPassed" class="daily-receipt-item">
        <b class="daily-receipt-verb">刚通过</b>
        <span class="daily-receipt-text">{{ lastPassed.zone_name }} · {{ lastPassed.item_name }} · {{ lastPassed.shift }}</span>
        <button type="button" class="btn daily-receipt-btn" :disabled="busy" @click="markTeaching">标为卫生教材</button>
      </span>
      <span v-if="teachingHint" class="daily-receipt-text">{{ teachingHint }}</span>
    </div>

    <div class="daily-grid">
      <div class="table-card">
        <div class="table-card-header">
          <i class="daily-led warn"></i>
          <h3>
            {{ isToday ? '今天等验收' : `${queueDate} 全部检查项` }}
            <span class="daily-hint">{{ listRows.length }}</span>
          </h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="errorText" class="roster-empty">加载失败，点上方「刷新」重试。</div>
        <div
          v-else-if="isToday ? !listRows.length && !pastPendingRows.length : !listRows.length"
          class="roster-empty"
        >{{ emptyText }}</div>
        <template v-else>
          <ul v-if="listRows.length" class="queue-list">
            <li v-for="row in listRows" :key="`${row.item_id}-${row.shift}`">
              <button
                type="button"
                class="queue-btn"
                :class="{ active: selected && selected.item_id === row.item_id && selected.shift === row.shift }"
                @click="selectRow(row)"
              >
                <strong>{{ row.zone_name }} · {{ row.item_name }}</strong>
                <span>
                  {{ row.shift }} · {{ row.submitter_name || row.submitter_phone || '拍摄人未知' }}
                  <em v-if="!isToday" class="queue-status" :class="statusClass(row.status)">{{ row.status }}</em>
                </span>
              </button>
            </li>
          </ul>
          <p v-else class="roster-empty">{{ emptyText }}</p>

          <!-- 漏验（隔夜验收）：过去几个营业日还没验的行，按天分组、各带条数；
               点开后对照图会带上那一天（`reviewDate`），通过 / 驳回也带上 `business_date`。 -->
          <div v-if="isToday && pastGroups.length" class="queue-past">
            <p class="queue-past-kicker">漏验 · 共 {{ pastPendingRows.length }} 项</p>
            <div v-for="group in pastGroups" :key="`past-${group.date}`" class="queue-group">
              <div class="queue-group-head">
                <h4>{{ group.title }} · {{ group.rows.length }} 项待补验</h4>
              </div>
              <ul class="queue-list">
                <li v-for="row in group.rows" :key="`${group.date}-${row.item_id}-${row.shift}`">
                  <button
                    type="button"
                    class="queue-btn"
                    :class="{ active: selected && selected.item_id === row.item_id && selected.shift === row.shift && selected.business_date === row.business_date }"
                    @click="selectRow(row)"
                  >
                    <strong>{{ row.zone_name }} · {{ row.item_name }}</strong>
                    <span>
                      {{ row.shift }} · {{ row.submitter_name || row.submitter_phone || '拍摄人未知' }}
                      <em class="queue-status" :class="statusClass(row.status)">{{ row.status }}</em>
                    </span>
                  </button>
                </li>
              </ul>
            </div>
          </div>
        </template>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <i class="daily-led sig"></i>
          <h3>{{ selected ? selected.item_name : '对照验收' }}</h3>
        </div>
        <div v-if="!selected" class="roster-empty">点一项，对照提交当时的标准图。</div>
        <div v-else-if="reviewMissing" class="roster-empty">
          「{{ selected.item_name }}」在这一天没有照片（未提交，或已经被清理）。
        </div>
        <!-- 对照接口失败（不是 404）：面板里给一句 + 重试，别让右侧整块空着看起来像卡死。 -->
        <div v-else-if="reviewError" class="roster-empty review-error">
          <p class="review-note">对照图没加载出来。</p>
          <button type="button" class="btn" :disabled="busy" @click="retryReview">重试</button>
        </div>
        <div v-else-if="review" class="review-body">
          <p class="review-meta">
            <template v-if="reviewDate">{{ reviewDate }} · </template>{{ selected.zone_name }} · {{ selected.shift }} · 拍摄人 {{ review.submitter_name || review.submitter_phone }}
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
      :prompt="{ label: '哪里不合格（必填，员工能看到）', placeholder: '写一句让他知道改什么，例如：台面还有油渍、角落没擦到', hint: '必填 · 员工照这句重拍', required: true, maxlength: 120 }"
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
  border-left: 3px solid var(--action);
  background: var(--wb-tint-08);
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
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: var(--r-full);
  font-size: 11px;
  font-style: normal;
  border: 1px solid var(--line);
  color: var(--ink-dim);
}
/* 状态 = 6px 圆点 + 小字；圆点取文字色，胶囊只做底色/边框。 */
.queue-status::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: var(--r-full);
  background: currentColor;
}
.queue-status.pending { color: var(--warn); border-color: var(--wb-warn-a45); }
.queue-status.passed { color: var(--ok); border-color: var(--wb-ok-a45); }
.queue-status.todo { color: var(--ink-faint); }
/* 漏验分组（隔夜验收）：组标题写日期 + 条数，组与组之间一条发丝线分开。 */
.queue-past { border-top: 1px solid var(--wb-ink-a08); }
.queue-past-kicker {
  margin: 0;
  padding: 10px 14px 0;
  font: 600 10px/1.4 var(--font-sans);
  letter-spacing: .08em;
  color: var(--warn);
}
.queue-past .queue-group + .queue-group { border-top: 1px dashed var(--wb-ink-a08); }
.queue-group-head { display: flex; align-items: center; gap: 6px; padding: 8px 14px 0; }
.queue-group-head h4 {
  margin: 0;
  font: 500 10.5px/1.4 var(--font-sans);
  color: var(--ink-dim);
}
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
/* 通过回执 / 刚通过：固定高度一行，内容换了行高也不变（连批时通过/驳回按钮不漂移）。 */
.daily-receipt {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 48px;
  min-height: 48px;
  overflow: hidden;
  white-space: nowrap;
  font-size: 12px;
}
.daily-receipt-item { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
.daily-receipt-verb { font: 600 11px/1 var(--font-sans); color: var(--ink-dim); }
.daily-receipt-verb.is-ok { color: var(--ok); }
.daily-receipt-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font: 400 10px/1.4 var(--font-mono);
  color: var(--ink-faint);
}
.daily-receipt-reason {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font: 400 10px/1.4 var(--font-mono);
  color: var(--danger-bright);
}
/* 44px 触控下限：≤430px 的全局 `.btn` 有 min-height 40px !important，这里要压过它。 */
.daily-receipt-btn { flex: 0 0 auto; min-height: 44px !important; border-radius: var(--r-1); }
/* 对照接口失败的面板内错误态：一句 + 重试（按钮 ≥44px）。 */
.review-error { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; padding: 14px; }
.review-error .btn { min-height: 44px !important; border-radius: var(--r-1); }

/* ── B 案版式（2026-10-17 结构重构）：页面头 + 面板 + 行列，替代旧的 hero 卡 + 按钮块 ── */
.daily-page { display: flex; flex-direction: column; gap: 16px; }
/* 页头不再是卡片：没有底色/边框/渐变，只留排版层级（共享表的 .roster-head.card 规则靠
   scoped 属性选择器的特异性被这里覆盖）。 */
.daily-page .roster-head {
  background: none;
  border: 0;
  box-shadow: none;
  padding: 0;
  display: flex;
  align-items: flex-end;
  gap: 16px;
}
.daily-page .roster-head::before { display: none; }
.daily-page .roster-head h1 {
  margin: 0;
  font: 600 26px/1.1 var(--font-sans);
  letter-spacing: .01em;
  color: var(--ink);
}
.daily-page .roster-head p {
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--ink-dim);
}
.daily-datebar { margin-left: auto; }
.daily-datebar .btn { min-height: 44px; border-radius: var(--r-1); }
.daily-led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; }
.daily-led.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.daily-led.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }
.daily-page .table-card-header {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 9px;
  padding: 11px 14px;
}
/* 共享表的老规则把面板头排成「标题贴右」（space-between + 无左侧元素时才成立）；
   B 案的面板头是「LED + 标题 + hint」一串左对齐。 */
.daily-page .table-card-header h3 {
  margin: 0;
  font: 600 12.5px/1 var(--font-sans);
  letter-spacing: .06em;
  color: var(--ink);
}
.daily-page .table-card-header h3 { flex: 0 1 auto; }
.daily-page .table-card-header h3 span { margin-left: 6px; }
.daily-page .daily-datebar { flex-wrap: wrap; justify-content: flex-end; }
.daily-page .daily-datebar .btn { flex: 0 0 auto; white-space: nowrap; }
.daily-hint { font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
/* 队列从「一叠按钮」改成「一张表」：两行网格 + 状态贴右。 */
.daily-page .queue-list { padding: 0; gap: 0; }
.daily-page .queue-btn {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  gap: 3px 10px;
  width: 100%;
  min-height: 56px;
  padding: 10px 14px;
  background: none;
  border: 0;
  border-bottom: 1px solid var(--wb-ink-a08);
  border-radius: 0;
  text-align: left;
}
.daily-page .queue-btn strong {
  grid-column: 1;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: .01em;
}
.daily-page .queue-btn > span {
  grid-column: 1;
  font: 400 9.5px/1 var(--font-mono);
  color: var(--ink-faint);
}
.daily-page .queue-btn > span > em { grid-column: 2; }
.daily-page .queue-btn em { align-self: center; }
.daily-page .queue-btn.active { background: var(--surface-2); box-shadow: inset 3px 0 0 var(--action); }
.daily-page .review-meta { font: 400 10px/1.6 var(--font-mono); color: var(--ink-faint); }
.daily-page .review-actions .btn { min-height: 44px; border-radius: var(--r-1); }
/* 空态在分组里也保持同一套留白（今天那一组空、漏验还有时用得上）。 */
.daily-page .roster-empty { margin: 0; }

/* 手机 390（审查实测）：页头左右分栏把说明挤成窄栏、眉标「DAILY · 当日对照」断成两行、
   日期与「今天/刷新」只有 35–38px。改成「标题独占一行 + 筛选条独占一行 + 控件 48px」。
   提示条仍然一行：窄屏只留动作按钮，项目名那截让位（行高固定，按钮不漂移）。 */
@media (max-width: 720px) {
  .daily-page .roster-head { flex-direction: column; align-items: flex-start; gap: 12px; }
  .daily-datebar { margin-left: 0; width: 100%; justify-content: flex-start; }
  .daily-page .daily-datebar .btn { min-height: 48px; }
  .daily-page .daily-datebar :deep(.dpx),
  .daily-page .daily-datebar :deep(input) { min-height: 48px; }
  .daily-page .hy-eyebrow { white-space: nowrap; }
}
@media (max-width: 560px) {
  .daily-receipt-text { display: none; }
}
</style>
