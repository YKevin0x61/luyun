<script setup>
import { nextTick, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import {
  HYGIENE_PERMISSIONS,
  HYGIENE_SHIFTS,
  hygienePermissionLabel,
  hygieneShiftLabel,
  rosterStatusLabel,
} from '../../utils/hygieneCopy'

const employees = ref([])
const zones = ref([])
const loading = ref(true)
const errorText = ref('')
const drafts = ref({})
const busyId = ref(null)
const disableTarget = ref(null)

// 员工入口：全仓只有导航栏指向 /hygiene-roster，没人知道店员该扫哪个地址。这里把
// 绝对 URL 和二维码一起摆出来，新店员不用管理员口述。
const staffEntryUrl = ref('')
const entryCopied = ref(false)
const qrCanvas = ref(null)

async function renderStaffEntry() {
  // 用当前 origin：门店可能是内网 IP、也可能是域名，写死哪个都会有一半人打不开。
  staffEntryUrl.value = `${window.location.origin}/hygiene`
  entryCopied.value = false
  await nextTick()
  if (!qrCanvas.value) return
  try {
    await QRCode.toCanvas(qrCanvas.value, staffEntryUrl.value, { width: 148, margin: 1 })
  } catch {
    // 画不出二维码不影响复制链接，静默降级（下面那行 URL 仍然可读）。
  }
}

async function copyStaffEntry() {
  try {
    await navigator.clipboard.writeText(staffEntryUrl.value)
    entryCopied.value = true
  } catch {
    // 内网明文 http 不是安全上下文，没有 clipboard API：URL 就在旁边，手动抄。
    entryCopied.value = false
  }
}

function draftFor(row) {
  return drafts.value[row.id]
}

async function loadRoster() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/roster')
    employees.value = data.employees || []
    zones.value = data.zones || []
    const next = {}
    for (const row of employees.value) {
      next[row.id] = {
        name: row.name || '',
        job_title: row.job_title || '',
        permission: row.permission,
        shift: row.shift || '白班',
        zone_id: row.zone_id || (zonesForShift('白班')[0] && zonesForShift('白班')[0].id) || '',
      }
    }
    drafts.value = next
  } catch (err) {
    errorText.value = err.message || '无法加载花名册'
  } finally {
    loading.value = false
  }
}

function zonesForShift(shift) {
  return zones.value.filter((zone) =>
    (zone.shifts || HYGIENE_SHIFTS).includes(shift),
  )
}

function onShiftChange(row) {
  const draft = drafts.value[row.id]
  if (!draft) return
  const allowed = zonesForShift(draft.shift)
  if (!allowed.some((zone) => String(zone.id) === String(draft.zone_id))) {
    draft.zone_id = allowed.length ? allowed[0].id : ''
  }
}

onMounted(loadRoster)
onMounted(renderStaffEntry)

useHygieneRealtime({
  id: 'hygiene-admin-roster',
  resources: ['roster', 'assignment'],
  pull: loadRoster,
})

async function approve(row) {
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/roster/${row.id}/approve`)
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '批准失败'
  } finally {
    busyId.value = null
  }
}

function askDisable(row) {
  disableTarget.value = row
}

async function confirmDisable() {
  const row = disableTarget.value
  disableTarget.value = null
  if (!row) return
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/roster/${row.id}/disable`)
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '停用失败'
  } finally {
    busyId.value = null
  }
}

async function enable(row) {
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/roster/${row.id}/enable`)
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '启用失败'
  } finally {
    busyId.value = null
  }
}

async function saveRow(row) {
  const draft = draftFor(row)
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.patch(`/api/hygiene/admin/roster/${row.id}`, {
      name: draft.name,
      job_title: draft.job_title,
      permission: draft.permission,
    })
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '保存失败'
  } finally {
    busyId.value = null
  }
}

async function changeAssignment(row) {
  const draft = draftFor(row)
  if (!draft) return
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/roster/${row.id}/assignment`, {
      shift: draft.shift,
      zone_id: Number(draft.zone_id),
    })
    await loadRoster()
  } catch (err) {
    errorText.value = err.message || '改区域和班次失败'
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div class="roster-page">
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Roster · 人员名册</p>
        <h1>卫生花名册</h1>
        <p>批准注册、维护姓名职位，并设置卫生权限。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>当天责任区和班次只能由超级管理员调整。停用后不能登录，但花名册记录保留，可重新启用。超级管理员是后台共享账号，不能从花名册提升。</p>
        </details>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="loadRoster">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>

    <div class="table-card">
      <div class="table-card-header">
        <h3>员工入口</h3>
      </div>
      <p class="editor-lead">店员用手机扫这个码进卫生系统，登录用手机号 + 密码。链接也可以直接在手机浏览器里打开。</p>
      <div class="staff-entry">
        <canvas ref="qrCanvas" class="staff-entry-qr" role="img" aria-label="员工入口二维码"></canvas>
        <div class="staff-entry-copy">
          <code>{{ staffEntryUrl }}</code>
          <button type="button" class="btn" @click="copyStaffEntry">
            {{ entryCopied ? '已复制' : '复制链接' }}
          </button>
        </div>
      </div>
    </div>

    <div class="table-card">
      <div class="table-card-header">
        <h3>员工 <span>{{ employees.length }}</span></h3>
      </div>
      <div v-if="loading" class="roster-empty">正在加载…</div>
      <div v-else-if="errorText" class="roster-empty">加载失败，点上方「刷新」重试。</div>
      <div v-else-if="!employees.length" class="roster-empty">还没有人注册。</div>
      <div v-else class="hy-person-list">
        <article v-for="row in employees" :key="row.id" class="hy-person">
          <div class="hy-person-top">
            <div class="roster-person-copy">
              <strong>{{ row.name || '未设置姓名' }}</strong>
              <span>{{ row.phone }}</span>
            </div>
            <span class="roster-status" :data-status="rosterStatusLabel(row)">
              {{ rosterStatusLabel(row) }}
            </span>
          </div>
          <div v-if="drafts[row.id]" class="hy-person-fields">
            <label>
              姓名
              <input
                v-model="drafts[row.id].name"
                class="input"
                type="text"
                maxlength="40"
                :disabled="busyId === row.id"
                placeholder="请输入真实姓名"
              >
            </label>
            <label>
              职位
              <input
                v-model="drafts[row.id].job_title"
                class="input"
                type="text"
                maxlength="40"
                :disabled="busyId === row.id"
                placeholder="头衔，比如领班"
              >
            </label>
            <label>
              卫生权限
              <select
                v-model="drafts[row.id].permission"
                class="select"
                :disabled="busyId === row.id"
              >
                <option v-for="perm in HYGIENE_PERMISSIONS" :key="perm" :value="perm">
                  {{ hygienePermissionLabel(perm) }}
                </option>
              </select>
            </label>
            <label>
              当天区域 · 现在 {{ row.zone_name || '未选' }}
              <select
                v-model="drafts[row.id].zone_id"
                class="select"
                :disabled="busyId === row.id"
              >
                <option value="">未选</option>
                <option v-for="zone in zonesForShift(drafts[row.id].shift)" :key="zone.id" :value="zone.id">
                  {{ zone.name }}
                </option>
              </select>
            </label>
            <label>
              当天班次 · 现在 {{ hygieneShiftLabel(row.shift) }}
              <span class="hy-person-shift">
                <select
                  v-model="drafts[row.id].shift"
                  class="select"
                  :disabled="busyId === row.id"
                  @change="onShiftChange(row)"
                >
                  <option v-for="shift in HYGIENE_SHIFTS" :key="shift" :value="shift">
                    {{ shift }}
                  </option>
                </select>
                <button
                  type="button"
                  class="btn"
                  :disabled="busyId === row.id || !drafts[row.id].zone_id"
                  @click="changeAssignment(row)"
                >改区域和班次</button>
              </span>
            </label>
          </div>
          <div class="hy-person-actions">
            <button
              v-if="!row.approved && !row.disabled"
              type="button"
              class="btn btn-primary"
              :disabled="busyId === row.id"
              @click="approve(row)"
            >批准</button>
            <button
              v-if="row.disabled"
              type="button"
              class="btn btn-primary"
              :disabled="busyId === row.id"
              @click="enable(row)"
            >启用</button>
            <button
              type="button"
              class="btn"
              :disabled="busyId === row.id"
              @click="saveRow(row)"
            >保存</button>
            <button
              v-if="!row.disabled"
              type="button"
              class="btn btn-danger"
              :disabled="busyId === row.id"
              @click="askDisable(row)"
            >停用</button>
          </div>
        </article>
      </div>
    </div>

    <ConfirmDialog
      v-if="disableTarget"
      title="停用员工"
      :message="`停用 ${disableTarget.name || disableTarget.phone} 后不能登录，花名册里仍能看到这个人。`"
      confirm-label="停用"
      danger
      @confirm="confirmDisable"
      @cancel="disableTarget = null"
    />
  </div>
</template>

<style scoped>
.roster-person-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
</style>
