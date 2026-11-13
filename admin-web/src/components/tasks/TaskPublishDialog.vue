<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { createTask, getTaskAssignees, updateTask, uploadTaskImage } from '../../api/tasks'
import {
  DEADLINE_DEFAULT,
  FREQUENCY_OPTIONS,
  POOL_OPTIONS,
  WEEKDAY_OPTIONS,
  buildAssignee,
  defaultDueDate,
  filterEmployees,
} from '../../utils/tasks'

// 发布 / 编辑任务（票 02 即时，票 06 起也承担定期任务）。
//
// 类型：`once`（截止日期 + 时刻）或 `recurring`（开始日期 + 结束可选 + 频率 +
// 每期固定 HH:mm）。其余字段（标题、说明、指派、照片张数、参考图）两种类型共用。
//
// 编辑沿用同一个弹层：`instance` 有值 = 编辑即时任务（后端只允许还没有任何提交时
// 改，改完同步实例快照）；`definition` 有值 = 编辑定期定义（只改定义，已生成的期
// 保留快照）。

const props = defineProps({
  open: { type: Boolean, default: false },
  instance: { type: Object, default: null },
  definition: { type: Object, default: null },
  defaultKind: { type: String, default: 'once' },
  staff: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'saved'])

const form = reactive({
  kind: 'once',
  title: '',
  body: '',
  mode: 'group',
  groupIds: [],
  poolKind: 'all_staff',
  zoneId: null,
  dueDate: '',
  deadline: DEADLINE_DEFAULT,
  frequencyKind: 'daily',
  weekday: 0,
  monthDay: 1,
  startDate: '',
  endDate: '',
  photoCount: 1,
  referenceCaptureId: null,
  wecomGroupPush: false,
})

const catalog = ref({ today: '', employees: [], zones: [] })
const loading = ref(false)
const busy = ref(false)
const uploading = ref(false)
const error = ref('')
const warning = ref('')
const search = ref('')
const referenceInputRef = ref(null)
let loadToken = 0

const filteredEmployees = computed(() => filterEmployees(catalog.value.employees, search.value))
const referenceUrl = computed(() =>
  form.referenceCaptureId ? `/api/tasks/images/${form.referenceCaptureId}` : '',
)
const submitLabel = computed(() => {
  if (busy.value) return '提交中…'
  if (props.instance || props.definition) return '保存修改'
  return form.kind === 'recurring' ? '创建定期任务' : '发布任务'
})

const dialogTitle = computed(() => {
  if (props.instance) return '编辑任务'
  if (props.definition) return '编辑定期任务'
  return form.kind === 'recurring' ? '新建定期任务' : '发布任务'
})

const canSubmit = computed(() => {
  if (busy.value || loading.value) return false
  if (!form.title.trim() || !form.deadline) return false
  if (form.mode === 'group' && !form.groupIds.length) return false
  if (form.mode === 'pool' && form.poolKind === 'zone_on_duty' && !form.zoneId) return false
  if (form.kind === 'once') return Boolean(form.dueDate)
  if (!form.startDate) return false
  if (form.endDate && form.endDate < form.startDate) return false
  if (form.frequencyKind === 'weekly') return Number.isInteger(Number(form.weekday))
  if (form.frequencyKind === 'monthly') {
    const day = Number(form.monthDay)
    return Number.isInteger(day) && day >= 1 && day <= 31
  }
  return form.frequencyKind === 'daily'
})

function blankForm(kind = 'once') {
  form.kind = kind
  form.title = ''
  form.body = ''
  form.mode = 'group'
  form.groupIds = []
  form.poolKind = 'all_staff'
  form.zoneId = null
  form.dueDate = defaultDueDate(catalog.value.today, DEADLINE_DEFAULT)
  form.deadline = DEADLINE_DEFAULT
  form.frequencyKind = 'daily'
  form.weekday = 0
  form.monthDay = 1
  form.startDate = catalog.value.today || ''
  form.endDate = ''
  form.photoCount = 1
  form.referenceCaptureId = null
  form.wecomGroupPush = false
  search.value = ''
}

function fillFromDefinition(definition) {
  form.kind = 'recurring'
  form.title = definition.title || ''
  form.body = definition.body || ''
  form.mode = definition.assignee_kind === 'pool' ? 'pool' : 'group'
  form.groupIds =
    definition.assignee_kind === 'group' ? (definition.assignees || []).map((item) => item.id) : []
  form.poolKind = definition.pool_kind || 'all_staff'
  form.zoneId = definition.pool_zone_id || null
  form.deadline = definition.deadline_hhmm || DEADLINE_DEFAULT
  form.frequencyKind = definition.frequency_kind || 'daily'
  form.weekday = Number(definition.weekday ?? 0)
  form.monthDay = Number(definition.month_day ?? 1)
  form.startDate = definition.start_date || catalog.value.today || ''
  form.endDate = definition.end_date || ''
  form.photoCount = Number(definition.photo_count) || 1
  form.referenceCaptureId = definition.reference_capture_id || null
  form.wecomGroupPush = Boolean(definition.wecom_group_push)
  search.value = ''
}

function fillFromInstance(instance) {
  form.kind = 'once'
  form.title = instance.title || ''
  form.body = instance.body || ''
  form.mode = instance.assignee_kind === 'pool' ? 'pool' : 'group'
  form.groupIds = instance.assignee_kind === 'group' ? (instance.assignees || []).map((item) => item.id) : []
  form.poolKind = instance.pool_kind || 'all_staff'
  form.zoneId = instance.pool_zone_id || null
  form.dueDate = instance.period_date || catalog.value.today || ''
  form.deadline = String(instance.deadline_at || '').slice(11, 16) || DEADLINE_DEFAULT
  form.photoCount = Number(instance.photo_count) || 1
  form.referenceCaptureId = instance.reference_capture_id || null
  form.wecomGroupPush = Boolean(instance.wecom_group_push)
  search.value = ''
}

async function loadCatalog() {
  const token = ++loadToken
  loading.value = true
  error.value = ''
  try {
    const data = await getTaskAssignees({ staff: props.staff })
    if (token !== loadToken) return
    catalog.value = {
      today: data.today || '',
      employees: Array.isArray(data.employees) ? data.employees : [],
      zones: Array.isArray(data.zones) ? data.zones : [],
    }
  } catch (err) {
    if (token !== loadToken) return
    error.value = err?.detail || '指派名单没读出来，请关闭后重开'
  } finally {
    if (token === loadToken) loading.value = false
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      loadToken += 1
      return
    }
    error.value = ''
    warning.value = ''
    // 先把表单填好，再读花名册：目录回来是异步的，晚一步再填会把用户已经
    // 打的字/改的标题冲掉（每次打开弹层都像被「重置」一次）。
    if (props.instance) fillFromInstance(props.instance)
    else if (props.definition) fillFromDefinition(props.definition)
    else blankForm(props.defaultKind || 'once')
    await loadCatalog()
    // today 只有目录接口知道；只补空的日期，不覆盖用户已经填的。
    if (!form.dueDate) form.dueDate = defaultDueDate(catalog.value.today, form.deadline)
    if (!form.startDate) form.startDate = catalog.value.today || ''
  },
  { immediate: true },
)

function toggleMember(employeeId) {
  const index = form.groupIds.indexOf(employeeId)
  if (index >= 0) form.groupIds.splice(index, 1)
  else form.groupIds.push(employeeId)
}

async function pickReference(event) {
  const file = event.target.files && event.target.files[0]
  event.target.value = ''
  if (!file) return
  uploading.value = true
  error.value = ''
  try {
    const result = await uploadTaskImage(file, { purpose: 'reference', staff: props.staff })
    form.referenceCaptureId = result?.image?.capture_id || null
  } catch (err) {
    error.value = err?.detail || '参考图上传失败，请重试'
  } finally {
    uploading.value = false
  }
}

function clearReference() {
  form.referenceCaptureId = null
}

async function submit() {
  if (!canSubmit.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const payload = {
      kind: form.kind,
      title: form.title.trim(),
      body: form.body.trim(),
      photo_count: Number(form.photoCount),
      reference_capture_id: form.referenceCaptureId || null,
      deadline_hhmm: form.deadline,
      wecom_group_push: Boolean(form.wecomGroupPush),
      assignee: buildAssignee({
        mode: form.mode,
        groupIds: form.groupIds,
        poolKind: form.poolKind,
        zoneId: form.zoneId,
      }),
    }
    if (form.kind === 'once') {
      payload.due_date = form.dueDate
    } else {
      payload.frequency_kind = form.frequencyKind
      payload.start_date = form.startDate
      payload.end_date = form.endDate || null
      if (form.frequencyKind === 'weekly') payload.weekday = Number(form.weekday)
      if (form.frequencyKind === 'monthly') payload.month_day = Number(form.monthDay)
    }
    let result = null
    if (props.instance) result = await updateTask(props.instance.definition_id, payload)
    else if (props.definition) result = await updateTask(props.definition.id, payload)
    else result = await createTask(payload, { staff: props.staff })
    if (result && result.warning) {
      // 任务已经建好了，只是群消息没发出去：留在弹层里把提示读完再关。
      warning.value = String(result.warning)
      return
    }
    emit('saved')
  } catch (err) {
    error.value = err?.detail || '发布失败，请重试'
  } finally {
    busy.value = false
  }
}

function finishAfterWarning() {
  warning.value = ''
  emit('saved')
}
</script>

<template>
  <div
    v-if="open"
    class="modal-overlay"
    role="dialog"
    aria-modal="true"
    aria-labelledby="task-publish-title"
    @click.self="emit('close')"
  >
    <div class="modal-box task-dialog">
      <div class="modal-header">
        <h3 id="task-publish-title">{{ dialogTitle }}</h3>
        <button class="btn btn-sm" type="button" @click="emit('close')">关闭</button>
      </div>

      <p v-if="loading" class="task-hint">正在读取花名册与工作区…</p>

      <div class="form-row">
        <label for="task-title">任务标题</label>
        <input
          id="task-title"
          v-model.trim="form.title"
          class="input"
          type="text"
          maxlength="200"
          placeholder="比如：把冷库门擦干净"
        />
      </div>

      <div class="form-row">
        <label for="task-body">说明（可选）</label>
        <textarea
          id="task-body"
          v-model="form.body"
          class="input task-textarea"
          rows="3"
          maxlength="2000"
          placeholder="做到什么程度、注意什么，写清楚验收才不扯皮"
        ></textarea>
      </div>

      <div v-if="!instance && !definition" class="form-row">
        <label>任务类型</label>
        <div class="task-mode" role="group" aria-label="任务类型">
          <button
            type="button"
            class="task-mode-btn"
            :class="{ 'is-on': form.kind === 'once' }"
            @click="form.kind = 'once'"
          >发一次</button>
          <button
            type="button"
            class="task-mode-btn"
            :class="{ 'is-on': form.kind === 'recurring' }"
            @click="form.kind = 'recurring'"
          >定期</button>
        </div>
        <p v-if="form.kind === 'recurring'" class="task-hint">
          到点生成一期，每期单独拍照、单独验收；改定义只影响还没生成的期。
        </p>
      </div>

      <div class="form-row">
        <label>指派给</label>
        <div class="task-mode" role="group" aria-label="指派方式">
          <button
            type="button"
            class="task-mode-btn"
            :class="{ 'is-on': form.mode === 'group' }"
            @click="form.mode = 'group'"
          >固定小组</button>
          <button
            type="button"
            class="task-mode-btn"
            :class="{ 'is-on': form.mode === 'pool' }"
            @click="form.mode = 'pool'"
          >员工池</button>
        </div>
      </div>

      <template v-if="form.mode === 'group'">
        <div class="form-row">
          <input
            v-model.trim="search"
            class="input"
            type="search"
            placeholder="搜名字或职位"
            aria-label="搜索员工"
          />
        </div>
        <div class="task-people" role="group" aria-label="选择员工">
          <label v-for="employee in filteredEmployees" :key="employee.id" class="task-person">
            <input
              type="checkbox"
              :checked="form.groupIds.includes(employee.id)"
              @change="toggleMember(employee.id)"
            />
            <span class="task-person-name">{{ employee.name }}</span>
            <span v-if="employee.job_title" class="task-person-job">{{ employee.job_title }}</span>
          </label>
          <p v-if="!filteredEmployees.length" class="task-hint">没有匹配的在职员工。</p>
        </div>
        <p class="task-hint">组里谁先交算谁完成；其他人会看到「已被 XX 完成」。</p>
      </template>

      <template v-else>
        <div class="form-row">
          <label for="task-pool">从哪个池里找人</label>
          <select id="task-pool" v-model="form.poolKind" class="select">
            <option v-for="option in POOL_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </div>
        <div v-if="form.poolKind === 'zone_on_duty'" class="form-row">
          <label for="task-zone">工作区</label>
          <select id="task-zone" v-model="form.zoneId" class="select">
            <option :value="null" disabled>选一个工作区</option>
            <option v-for="zone in catalog.zones" :key="zone.id" :value="zone.id">{{ zone.name }}</option>
          </select>
        </div>
        <p class="task-hint">
          池任务不认领，先交为准；截止那天按排班实时解析可交名单。池里没人也可以发布。
        </p>
      </template>

      <template v-if="form.kind === 'once'">
        <div class="task-grid">
          <div class="form-row">
            <label for="task-due">截止日期</label>
            <input
              id="task-due"
              v-model="form.dueDate"
              class="input"
              type="date"
              :min="catalog.today"
            />
          </div>
          <div class="form-row">
            <label for="task-deadline">截止时刻</label>
            <input id="task-deadline" v-model="form.deadline" class="input" type="time" />
          </div>
          <div class="form-row">
            <label for="task-photos">完成照张数</label>
            <select id="task-photos" v-model.number="form.photoCount" class="select">
              <option :value="1">1 张</option>
              <option :value="2">2 张</option>
              <option :value="3">3 张</option>
            </select>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="task-grid">
          <div class="form-row">
            <label for="task-start">开始日期</label>
            <input id="task-start" v-model="form.startDate" class="input" type="date" />
          </div>
          <div class="form-row">
            <label for="task-end">结束日期（可选）</label>
            <input
              id="task-end"
              v-model="form.endDate"
              class="input"
              type="date"
              :min="form.startDate"
            />
          </div>
          <div class="form-row">
            <label for="task-deadline">每期截止时刻</label>
            <input id="task-deadline" v-model="form.deadline" class="input" type="time" />
          </div>
        </div>
        <div class="task-grid">
          <div class="form-row">
            <label for="task-frequency">频率</label>
            <select id="task-frequency" v-model="form.frequencyKind" class="select">
              <option v-for="option in FREQUENCY_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </div>
          <div v-if="form.frequencyKind === 'weekly'" class="form-row">
            <label for="task-weekday">星期几</label>
            <select id="task-weekday" v-model.number="form.weekday" class="select">
              <option v-for="option in WEEKDAY_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </div>
          <div v-if="form.frequencyKind === 'monthly'" class="form-row">
            <label for="task-month-day">每月几号</label>
            <input
              id="task-month-day"
              v-model.number="form.monthDay"
              class="input"
              type="number"
              min="1"
              max="31"
            />
          </div>
          <div class="form-row">
            <label for="task-photos">完成照张数</label>
            <select id="task-photos" v-model.number="form.photoCount" class="select">
              <option :value="1">1 张</option>
              <option :value="2">2 张</option>
              <option :value="3">3 张</option>
            </select>
          </div>
        </div>
        <p class="task-hint">
          从开始日期起按频率生成；每月 29/30/31 号遇到没有这一天的月份会跳过。
        </p>
      </template>

      <div class="form-row">
        <label>参考图（可选，员工拍之前先看）</label>
        <div v-if="referenceUrl" class="task-reference">
          <img :src="referenceUrl" alt="参考图" />
          <button type="button" class="task-link" @click="clearReference">移除</button>
        </div>
        <div v-else class="task-reference-actions">
          <input
            ref="referenceInputRef"
            class="task-file"
            type="file"
            accept="image/*"
            @change="pickReference"
          />
          <button
            type="button"
            class="task-secondary"
            :disabled="uploading"
            @click="referenceInputRef && referenceInputRef.click()"
          >{{ uploading ? '上传中…' : '上传参考图' }}</button>
        </div>
      </div>

      <label class="task-check">
        <input v-model="form.wecomGroupPush" type="checkbox" />
        <span>同时发企业微信群</span>
        <span class="task-hint">（默认不勾；没有配置群订阅时会提示）</span>
      </label>

      <div v-if="warning" class="task-warning" role="alert">
        <p>{{ warning }}</p>
        <button type="button" class="task-secondary" @click="finishAfterWarning">知道了</button>
      </div>

      <p v-if="error" class="task-error" role="alert">{{ error }}</p>

      <div class="modal-footer">
        <button class="btn" type="button" @click="emit('close')">取消</button>
        <button
          class="task-primary"
          type="button"
          :disabled="!canSubmit"
          @click="submit"
        >{{ submitLabel }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.task-dialog {
  width: min(560px, 100%);
}
.task-textarea {
  resize: vertical;
  min-height: 76px;
  line-height: 1.6;
}
.task-mode {
  display: flex;
  gap: 8px;
}
.task-mode-btn {
  flex: 1;
  padding: 7px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
  color: var(--ink-dim);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.task-mode-btn.is-on {
  border-color: var(--action);
  background: var(--action-soft);
  color: var(--action);
  font-weight: 700;
}
.task-people {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 2px 10px;
  max-height: 188px;
  overflow-y: auto;
  padding: 6px 8px;
  margin-bottom: 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-1);
  background: var(--surface-2);
}
@media (max-width: 560px) {
  .task-people {
    grid-template-columns: minmax(0, 1fr);
  }
}
.task-person {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 2px;
  font-size: 13px;
  color: var(--ink);
  cursor: pointer;
}
.task-person input {
  accent-color: var(--action);
}
.task-person-name {
  font-weight: 600;
}
.task-person-job {
  font-size: 11px;
  color: var(--ink-faint);
}
.task-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
@media (max-width: 560px) {
  .task-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
.task-hint {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-faint);
}
.task-reference {
  display: flex;
  align-items: center;
  gap: 10px;
}
.task-reference img {
  width: 96px;
  height: 72px;
  object-fit: cover;
  border-radius: var(--r-1);
  border: 1px solid var(--line);
}
.task-link {
  border: 0;
  background: none;
  color: var(--action);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
.task-file {
  display: none;
}
.task-reference-actions {
  display: flex;
  gap: 8px;
}
.task-secondary,
.task-primary {
  min-height: var(--tap);
  padding: 6px 14px;
  border-radius: var(--r-1);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.task-secondary {
  border: 1px solid var(--line-strong);
  background: var(--surface-2);
  color: var(--ink-dim);
}
.task-primary {
  border: 1px solid var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 700;
}
.task-primary:disabled,
.task-secondary:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.task-check {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 14px;
  color: var(--ink);
}
.task-warning {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: var(--r-2);
  background: var(--warn-soft);
  color: var(--ink);
}
.task-warning p {
  margin: 0;
  line-height: 1.6;
}
.task-warning button {
  flex: 0 0 auto;
  white-space: nowrap;
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
</style>
