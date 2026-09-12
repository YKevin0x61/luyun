<script setup>
import { onMounted, ref } from 'vue'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { api } from '../../api/client'
import {
  HYGIENE_PERMISSIONS,
  hygienePermissionLabel,
  rosterStatusLabel,
} from '../../utils/hygieneCopy'

const employees = ref([])
const loading = ref(true)
const errorText = ref('')
const drafts = ref({})
const busyId = ref(null)
const disableTarget = ref(null)

function draftFor(row) {
  return drafts.value[row.id]
}

async function loadRoster() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/roster')
    employees.value = data.employees || []
    const next = {}
    for (const row of employees.value) {
      next[row.id] = {
        job_title: row.job_title || '',
        permission: row.permission,
      }
    }
    drafts.value = next
  } catch (err) {
    errorText.value = err.message || '无法加载花名册'
  } finally {
    loading.value = false
  }
}

onMounted(loadRoster)

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

async function saveRow(row) {
  const draft = draftFor(row)
  busyId.value = row.id
  errorText.value = ''
  try {
    await api.patch(`/api/hygiene/admin/roster/${row.id}`, {
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
</script>

<template>
  <div class="roster-page">
    <div class="card roster-head">
      <div>
        <h2>卫生花名册</h2>
        <p>批准新注册、写职位、把人设成普通员工或管理员。停用后不能登录，行还留在这里。超级管理员仍是后台共享账号，不能从花名册升上去。</p>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="loadRoster">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>

    <div class="table-card">
      <div class="table-card-header">
        <h3>员工 <span>{{ employees.length }}</span></h3>
      </div>
      <div v-if="loading" class="roster-empty">正在加载…</div>
      <div v-else-if="!employees.length" class="roster-empty">还没有人注册。</div>
      <div v-else class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>手机号</th>
              <th>职位</th>
              <th>卫生权限</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in employees" :key="row.id">
              <td class="roster-phone">{{ row.phone }}</td>
              <td>
                <input
                  v-if="drafts[row.id]"
                  v-model="drafts[row.id].job_title"
                  class="input"
                  type="text"
                  maxlength="40"
                  :disabled="busyId === row.id"
                  placeholder="头衔，比如领班"
                  aria-label="职位"
                >
              </td>
              <td>
                <select
                  v-if="drafts[row.id]"
                  v-model="drafts[row.id].permission"
                  class="select"
                  :disabled="busyId === row.id"
                  aria-label="卫生权限"
                >
                  <option v-for="perm in HYGIENE_PERMISSIONS" :key="perm" :value="perm">
                    {{ hygienePermissionLabel(perm) }}
                  </option>
                </select>
              </td>
              <td>
                <span class="roster-status" :data-status="rosterStatusLabel(row)">
                  {{ rosterStatusLabel(row) }}
                </span>
              </td>
              <td class="roster-actions">
                <button
                  v-if="!row.approved && !row.disabled"
                  type="button"
                  class="btn btn-primary btn-sm"
                  :disabled="busyId === row.id"
                  @click="approve(row)"
                >批准</button>
                <button
                  type="button"
                  class="btn btn-sm"
                  :disabled="busyId === row.id"
                  @click="saveRow(row)"
                >保存</button>
                <button
                  v-if="!row.disabled"
                  type="button"
                  class="btn btn-danger btn-sm"
                  :disabled="busyId === row.id"
                  @click="askDisable(row)"
                >停用</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <ConfirmDialog
      v-if="disableTarget"
      title="停用员工"
      :message="`停用 ${disableTarget.phone} 后不能登录，花名册里仍能看到这个人。`"
      confirm-label="停用"
      danger
      @confirm="confirmDisable"
      @cancel="disableTarget = null"
    />
  </div>
</template>

<style scoped>
.roster-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 1100px;
}
.roster-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
}
.roster-head h2 {
  margin: 0 0 6px;
  font-size: 16px;
}
.roster-head p {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
  max-width: 52em;
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
.roster-phone { font-variant-numeric: tabular-nums; }
.roster-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.roster-status[data-status='待批准'] { color: var(--yellow); }
.roster-status[data-status='已批准'] { color: var(--green); }
.roster-status[data-status='已停用'] { color: var(--text-dim); }
.data-table .input,
.data-table .select {
  min-width: 120px;
  width: 100%;
}
</style>
