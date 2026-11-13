<script setup>
import { computed, ref, watch } from 'vue'
import { acceptTask, cancelTask, getTaskInstance, getTaskAssignees, reassignTask, rejectTask } from '../../api/tasks'
import { POOL_OPTIONS, assigneeEmptyHint, assigneeText, filterEmployees, formatDeadlineLabel, photoHint, statusLabel, statusTone } from '../../utils/tasks'

// 任务详情（票 02）：列表点进来的只读弹层，发布者/有任务管理能力的人可以在
// 「还没有提交」时编辑或撤回。票 03 起这里再长出提交照片与验收区。

const props = defineProps({
  open: { type: Boolean, default: false },
  instance: { type: Object, default: null },
  staff: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'edit', 'changed', 'work'])

const detail = ref(null)
const loading = ref(false)
const error = ref('')
const actionBusy = ref(false)
const confirmingCancel = ref(false)
let loadToken = 0

const tone = computed(() => statusTone(detail.value))
const emptyAssignees = computed(() => assigneeEmptyHint(detail.value))
const submissions = computed(() => (Array.isArray(detail.value?.submissions) ? detail.value.submissions : []))
const pendingSubmission = computed(
  () => submissions.value.find((item) => item.status === 'pending') || null,
)
const rejecting = ref(false)
const rejectReason = ref('')
const previewPhoto = ref('')
const canReview = computed(() => Boolean(detail.value?.can_review))
const resignedNames = computed(() =>
  (Array.isArray(detail.value?.assignees) ? detail.value.assignees : [])
    .filter((member) => member.resigned)
    .map((member) => member.name),
)

// 改派（票 08）：待交且没有待验收提交时，管理能力者可以把这一期换给另一组/另一个池。
const reassigning = ref(false)
const reassignMode = ref('group')
const reassignPoolKind = ref('all_staff')
const reassignZoneId = ref('')
const reassignSelected = ref([])
const reassignKeyword = ref('')
const reassignBusy = ref(false)
const reassignCatalog = ref({ today: '', employees: [], zones: [] })
const reassignCatalogLoading = ref(false)
const reassignError = ref('')
const catalogEmployees = computed(() =>
  filterEmployees(reassignCatalog.value.employees || [], reassignKeyword.value),
)

async function openReassign() {
  reassigning.value = true
  reassignError.value = ''
  reassignKeyword.value = ''
  reassignMode.value = detail.value?.assignee_kind === 'pool' ? 'pool' : 'group'
  reassignPoolKind.value = detail.value?.pool_kind || 'all_staff'
  reassignZoneId.value = detail.value?.pool_zone_id ? String(detail.value.pool_zone_id) : ''
  reassignSelected.value = (Array.isArray(detail.value?.assignees) ? detail.value.assignees : [])
    .filter((member) => !member.resigned)
    .map((member) => member.id)
  reassignCatalogLoading.value = true
  try {
    reassignCatalog.value = await getTaskAssignees({ staff: props.staff })
  } catch (err) {
    reassignError.value = err?.detail || '人员名单没读出来，请重试'
  } finally {
    reassignCatalogLoading.value = false
  }
}

function toggleReassignMember(employeeId) {
  const id = Number(employeeId)
  const index = reassignSelected.value.indexOf(id)
  if (index === -1) reassignSelected.value.push(id)
  else reassignSelected.value.splice(index, 1)
}

async function submitReassign() {
  if (!detail.value || reassignBusy.value) return
  reassignError.value = ''
  let assignee
  if (reassignMode.value === 'group') {
    if (!reassignSelected.value.length) {
      reassignError.value = '至少选一位员工，或者改选一个池'
      return
    }
    assignee = { kind: 'group', employee_ids: reassignSelected.value }
  } else {
    assignee = { kind: 'pool', pool_kind: reassignPoolKind.value }
    if (reassignPoolKind.value === 'zone_on_duty') {
      if (!reassignZoneId.value) {
        reassignError.value = '选了「当天在某工作区当班」，还要选一个工作区'
        return
      }
      assignee.zone_id = Number(reassignZoneId.value)
    }
  }
  reassignBusy.value = true
  try {
    await reassignTask(detail.value.id, assignee, { staff: props.staff })
    reassigning.value = false
    await load()
    emit('changed')
  } catch (err) {
    reassignError.value = err?.detail || '改派失败，请重试'
  } finally {
    reassignBusy.value = false
  }
}

async function load() {
  if (!props.instance) return
  const token = ++loadToken
  loading.value = true
  error.value = ''
  confirmingCancel.value = false
  try {
    const data = await getTaskInstance(props.instance.id, { staff: props.staff })
    if (token !== loadToken) return
    detail.value = data
  } catch (err) {
    if (token !== loadToken) return
    detail.value = null
    error.value = err?.detail || '任务没读出来，请关闭后重开'
  } finally {
    if (token === loadToken) loading.value = false
  }
}

watch(
  () => [props.open, props.instance?.id],
  ([open]) => {
    if (open) {
      detail.value = props.instance
      load()
    } else {
      loadToken += 1
      detail.value = null
      confirmingCancel.value = false
      rejecting.value = false
      rejectReason.value = ''
      previewPhoto.value = ''
      reassigning.value = false
      reassignError.value = ''
    }
  },
  { immediate: true },
)

function requestCancel() {
  if (!confirmingCancel.value) {
    confirmingCancel.value = true
    return
  }
  doCancel()
}

async function doCancel() {
  if (!detail.value || actionBusy.value) return
  actionBusy.value = true
  error.value = ''
  try {
    await cancelTask(detail.value.id)
    emit('changed')
    emit('close')
  } catch (err) {
    error.value = err?.detail || '撤回失败，请重试'
  } finally {
    actionBusy.value = false
  }
}

function requestEdit() {
  if (!detail.value) return
  emit('edit', detail.value)
}

function openPreview(url) {
  previewPhoto.value = url
}

async function doAccept() {
  if (!detail.value || actionBusy.value) return
  actionBusy.value = true
  error.value = ''
  try {
    await acceptTask(detail.value.id, { staff: props.staff })
    await load()
    emit('changed')
  } catch (err) {
    error.value = err?.detail || '验收失败，请刷新重试'
  } finally {
    actionBusy.value = false
  }
}

async function doReject() {
  if (!detail.value || actionBusy.value) return
  const reason = rejectReason.value.trim()
  if (!reason) {
    error.value = '驳回要写清原因，员工才知道怎么改'
    return
  }
  if (reason.length > 200) {
    error.value = `驳回原因最多 200 字，现在 ${reason.length} 字`
    return
  }
  actionBusy.value = true
  error.value = ''
  try {
    await rejectTask(detail.value.id, reason, { staff: props.staff })
    rejecting.value = false
    rejectReason.value = ''
    await load()
    emit('changed')
  } catch (err) {
    error.value = err?.detail || '驳回失败，请刷新重试'
  } finally {
    actionBusy.value = false
  }
}
</script>

<template>
  <div
    v-if="open"
    class="modal-overlay"
    role="dialog"
    aria-modal="true"
    aria-labelledby="task-detail-title"
    @click.self="emit('close')"
  >
    <div class="modal-box task-dialog">
      <div class="modal-header">
        <h3 id="task-detail-title">任务详情</h3>
        <button class="btn btn-sm" type="button" @click="emit('close')">关闭</button>
      </div>

      <p v-if="loading && !detail" class="task-hint">正在读取任务…</p>
      <p v-if="error" class="task-error" role="alert">{{ error }}</p>

      <template v-if="detail">
        <header class="task-detail-head">
          <h4>{{ detail.title }}</h4>
          <span class="task-badge" :class="`is-${tone}`">{{ statusLabel(detail) }}</span>
        </header>

        <dl class="task-detail-meta">
          <div>
            <dt>执行人</dt>
            <dd>
              {{ assigneeText(detail) }}
              <span v-if="resignedNames.length" class="task-resigned">
                {{ resignedNames.join('、') }} 已离职
              </span>
            </dd>
          </div>
          <div>
            <dt>截止</dt>
            <dd>{{ formatDeadlineLabel(detail.deadline_at) }}</dd>
          </div>
          <div>
            <dt>照片</dt>
            <dd>{{ photoHint(detail) }}</dd>
          </div>
          <div>
            <dt>发布</dt>
            <dd>{{ detail.created_by?.name || '—' }}</dd>
          </div>
        </dl>

        <p v-if="emptyAssignees" class="task-warn">该日无可指派人员</p>
        <p v-if="detail.needs_reassign && detail.can_reassign" class="task-warn">
          有负责人已离职，请改派给还在职的人。
        </p>
        <p v-if="detail.status === 'pending' && detail.claimed_by_me" class="task-hint">
          你已提交，等验收结果。
        </p>
        <p
          v-else-if="detail.status === 'pending' && detail.pending_submitter_name"
          class="task-hint"
        >已被 {{ detail.pending_submitter_name }} 完成，等验收结果。</p>
        <p v-if="detail.last_reject_reason && detail.status === 'todo'" class="task-reject">
          上次被驳回：{{ detail.last_reject_reason }}
        </p>

        <p v-if="detail.body" class="task-detail-body">{{ detail.body }}</p>

        <div v-if="detail.reference_image_url" class="task-reference">
          <img :src="detail.reference_image_url" alt="参考图" />
          <span class="task-hint">发布者提供的参考图</span>
        </div>

        <p v-if="detail.submitted_count && detail.status === 'todo'" class="task-hint">
          已提交 {{ detail.submitted_count }} 次（旧提交在驳回后可重新提交，历史保留）。
        </p>

        <section v-if="submissions.length" class="task-submissions">
          <h5>提交记录</h5>
          <div v-for="item in submissions" :key="item.id" class="task-submission">
            <p class="task-submission-row">
              {{ item.submitter_name || '员工' }} · {{ formatDeadlineLabel(item.submitted_at) }} ·
              {{ item.status === 'pending' ? '待验收' : item.status === 'passed' ? '已通过' : item.status === 'rejected' ? '已驳回' : item.status }}
              <span v-if="item.late"> · 逾期提交</span>
            </p>
            <p v-if="item.reject_reason" class="task-submission-reject">驳回理由：{{ item.reject_reason }}</p>
            <div v-if="item.photos && item.photos.length" class="task-submission-photos">
              <img
                v-for="photo in item.photos"
                :key="photo.capture_id"
                :src="photo.url"
                alt="完成照"
                @click="openPreview(photo.url)"
              />
            </div>
          </div>
        </section>

        <section v-if="reassigning" class="task-reassign">
          <h5>改派给</h5>
          <div class="task-reassign-mode">
            <button
              type="button"
              :class="{ 'is-on': reassignMode === 'group' }"
              @click="reassignMode = 'group'"
            >固定小组</button>
            <button
              type="button"
              :class="{ 'is-on': reassignMode === 'pool' }"
              @click="reassignMode = 'pool'"
            >员工池</button>
          </div>
          <template v-if="reassignMode === 'group'">
            <input
              v-model="reassignKeyword"
              class="task-reassign-search"
              type="search"
              placeholder="搜名字或职位"
            />
            <div class="task-reassign-members">
              <label
                v-for="employee in catalogEmployees"
                :key="employee.id"
                class="task-reassign-member"
              >
                <input
                  type="checkbox"
                  :checked="reassignSelected.includes(employee.id)"
                  @change="toggleReassignMember(employee.id)"
                />
                {{ employee.name }}
              </label>
              <p v-if="reassignCatalogLoading" class="task-hint">正在读取人员…</p>
              <p v-else-if="!catalogEmployees.length" class="task-hint">没有匹配的员工。</p>
            </div>
          </template>
          <template v-else>
            <select v-model="reassignPoolKind" class="task-reassign-select">
              <option v-for="option in POOL_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
            <select
              v-if="reassignPoolKind === 'zone_on_duty'"
              v-model="reassignZoneId"
              class="task-reassign-select"
            >
              <option value="">选工作区</option>
              <option v-for="zone in reassignCatalog.zones || []" :key="zone.id" :value="String(zone.id)">
                {{ zone.name }}
              </option>
            </select>
          </template>
          <p v-if="reassignError" class="task-error" role="alert">{{ reassignError }}</p>
        </section>

        <div v-if="canReview" class="task-review">
          <p class="task-hint">
            待验收：{{ pendingSubmission?.submitter_name || '员工' }} 提交于
            {{ formatDeadlineLabel(pendingSubmission?.submitted_at) }}
            <span v-if="pendingSubmission?.late">（逾期提交）</span>
          </p>
          <div v-if="pendingSubmission?.photos?.length" class="task-review-photos">
            <img
              v-for="photo in pendingSubmission.photos"
              :key="photo.capture_id"
              :src="photo.url"
              alt="待验收照片"
              @click="openPreview(photo.url)"
            />
          </div>
          <template v-if="rejecting">
            <textarea
              v-model="rejectReason"
              class="task-review-reason"
              rows="3"
              maxlength="200"
              placeholder="驳回原因（必填，最多 200 字）：员工会看到并重拍"
            ></textarea>
            <p class="task-hint">{{ rejectReason.length }}/200</p>
          </template>
        </div>

        <div class="modal-footer">
          <button class="btn" type="button" @click="emit('close')">关闭</button>
          <template v-if="canReview">
            <button
              v-if="!rejecting"
              class="task-secondary"
              type="button"
              :disabled="actionBusy"
              @click="rejecting = true"
            >驳回</button>
            <button
              v-else
              class="task-secondary"
              type="button"
              :disabled="actionBusy"
              @click="doReject"
            >{{ actionBusy ? '驳回中…' : '确认驳回' }}</button>
            <button
              class="task-primary"
              type="button"
              :disabled="actionBusy || rejecting"
              @click="doAccept"
            >{{ actionBusy ? '处理中…' : '通过验收' }}</button>
          </template>
          <button
            v-if="detail.can_submit"
            class="task-primary"
            type="button"
            @click="emit('work', detail)"
          >去完成</button>
          <template v-if="reassigning">
            <button class="task-secondary" type="button" :disabled="reassignBusy" @click="reassigning = false">取消改派</button>
            <button class="task-primary" type="button" :disabled="reassignBusy" @click="submitReassign">
              {{ reassignBusy ? '改派中…' : '确认改派' }}
            </button>
          </template>
          <template v-else>
            <button
              v-if="detail.can_reassign"
              class="task-secondary"
              type="button"
              @click="openReassign"
            >改派</button>
            <button
              v-if="detail.can_edit"
              class="task-secondary"
              type="button"
              @click="requestEdit"
            >编辑</button>
          </template>
          <button
            v-if="detail.can_cancel && !reassigning"
            class="task-danger"
            type="button"
            :disabled="actionBusy"
            @click="requestCancel"
          >{{ confirmingCancel ? '确定撤回？' : '撤回任务' }}</button>
        </div>
      </template>
    </div>
    <div v-if="previewPhoto" class="task-lightbox" @click="previewPhoto = ''">
      <img :src="previewPhoto" alt="完成照大图" />
    </div>
  </div>
</template>

<style scoped>
.task-dialog {
  width: min(560px, 100%);
}
.task-review {
  margin-top: 12px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
}
.task-review-photos {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.task-review-photos img {
  width: 88px;
  height: 88px;
  object-fit: cover;
  border-radius: var(--r-1);
  border: 1px solid var(--line);
  cursor: zoom-in;
}
.task-review-reason {
  width: 100%;
  box-sizing: border-box;
  padding: 8px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
}
.task-lightbox {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--scrim);
  cursor: zoom-out;
}
.task-lightbox img {
  max-width: 100%;
  max-height: 92vh;
  object-fit: contain;
  border-radius: var(--r-1);
}
.task-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.task-detail-head h4 {
  margin: 0;
  font-size: 16px;
  line-height: 1.5;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.task-badge {
  flex: none;
  padding: 1px 8px;
  border-radius: var(--r-full);
  font-size: 11px;
  font-weight: 700;
  line-height: 1.7;
  background: var(--action-soft);
  color: var(--action);
}
.task-badge.is-danger {
  background: var(--danger-soft);
  color: var(--danger-bright);
}
.task-badge.is-warn {
  background: var(--warn-soft);
  color: var(--warn);
}
.task-badge.is-ok {
  background: var(--ok-soft);
  color: var(--ok);
}
.task-badge.is-muted {
  background: var(--surface-2);
  color: var(--ink-faint);
}
.task-detail-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0 0 10px;
}
.task-detail-meta > div {
  display: flex;
  gap: 10px;
  font-size: 13px;
  line-height: 1.6;
}
.task-detail-meta dt {
  flex: none;
  width: 48px;
  color: var(--ink-faint);
}
.task-detail-meta dd {
  margin: 0;
  color: var(--ink-dim);
  overflow-wrap: anywhere;
}
.task-detail-body {
  margin: 0 0 10px;
  padding: 10px 12px;
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink-dim);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}
.task-reference {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.task-reference img {
  width: 120px;
  height: 90px;
  object-fit: cover;
  border-radius: var(--r-1);
  border: 1px solid var(--line);
}
.task-warn {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--warn);
}
.task-hint {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-faint);
}
.task-submissions {
  margin: 0 0 10px;
}
.task-submissions h5 {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--ink-faint);
}
.task-submission {
  margin-bottom: 8px;
}
.task-submission-row {
  margin: 0 0 2px;
  font-size: 12px;
  color: var(--ink-dim);
}
.task-submission-reject {
  margin: 0 0 4px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--danger-bright);
}
.task-submission-photos {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.task-submission-photos img {
  width: 88px;
  height: 66px;
  object-fit: cover;
  border-radius: var(--r-1);
  border: 1px solid var(--line);
}
.task-reject {
  margin: 0 0 8px;
  padding: 8px 10px;
  border-radius: var(--r-1);
  background: var(--danger-soft);
  color: var(--danger-bright);
  font-size: 12px;
  line-height: 1.6;
}
.task-primary,
.task-secondary,
.task-danger {
  min-height: var(--tap);
  padding: 6px 14px;
  border-radius: var(--r-1);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.task-primary {
  border: 1px solid var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 700;
}
.task-secondary {
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink-dim);
}
.task-danger {
  border: 1px solid var(--danger);
  background: var(--danger-soft);
  color: var(--danger-bright);
  font-weight: 700;
}
.task-secondary:disabled,
.task-danger:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.task-error {
  margin: 0 0 8px;
  padding: 8px 10px;
  border-radius: var(--r-1);
  background: var(--danger-soft);
  color: var(--danger-bright);
  font-size: 12px;
  line-height: 1.6;
}

.task-resigned {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: var(--r-full);
  background: var(--danger-soft);
  color: var(--danger-bright);
  font-size: 11px;
  font-weight: 700;
}
.task-reassign {
  margin-top: 12px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
}
.task-reassign h5 {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--ink);
}
.task-reassign-mode {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.task-reassign-mode button {
  flex: 1;
  min-height: var(--tap);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink-dim);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.task-reassign-mode button.is-on {
  border-color: var(--action);
  background: var(--action-soft);
  color: var(--action);
  font-weight: 700;
}
.task-reassign-search,
.task-reassign-select {
  width: 100%;
  box-sizing: border-box;
  min-height: var(--tap);
  margin-bottom: 8px;
  padding: 6px 8px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: var(--surface-1);
  color: var(--ink);
  font: inherit;
  font-size: 13px;
}
.task-reassign-members {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 4px 10px;
}
.task-reassign-member {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  color: var(--ink-dim);
}
</style>
