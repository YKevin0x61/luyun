<script setup>
/**
 * 仪容仪表（票 12）：今天谁要拍、各自到哪一步，以及那张标准图。
 *
 * 名单来自**排班**（服务端 `admin_day` 给的）：休假的与今天没排到的人不在这张表上，
 * 所以这里的数字就是真实要拍的人数 —— 不用再跟花名册对一次，也不会出现「名单上有人
 * 但今天根本没上班」。
 *
 * **没有钟点**：这一项不设截止时间（用户口径），所以页面上没有「逾期」那一栏，
 * 也没有催办。要处理的只是「等验收」那一堆。
 *
 * 驳回必须写原因：员工照着那句话重拍（跟日常驳回同一条口径）。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
import { formatHygieneStamp } from '../../utils/hygieneTime'

const STATUS_LABEL = {
  todo: '待拍',
  pending: '等验收',
  passed: '已通过',
  rejected: '已驳回',
}

const day = ref({ business_date: '', has_standard: false, standard_updated_at: '', counts: {}, people: [] })
const loading = ref(true)
const errorText = ref('')
const busyId = ref(null)
const rejectFor = ref(null)
const rejectNote = ref('')
const notice = ref('')

const imageUploads = useImageUploadQueueStore()

const counts = computed(() => day.value.counts || {})
const people = computed(() => day.value.people || [])

onMounted(() => refresh())

useHygieneRealtime({
  id: 'hygiene-admin-attire',
  resources: ['attire'],
  pull: () => refresh(true),
})

async function refresh(quiet = false) {
  if (!quiet) loading.value = true
  errorText.value = ''
  try {
    day.value = await api.get('/api/hygiene/admin/attire')
  } catch (err) {
    errorText.value = err.message || '无法加载仪容仪表'
  } finally {
    loading.value = false
  }
}

function personLabel(row) {
  return row.name || `员工 ${row.employee_id}`
}

function stamp(value) {
  return value ? formatHygieneStamp(value) : ''
}

/** 那张实拍的地址。`variant=preview` 给列表用，原图在灯箱里看。 */
function shotUrl(row, variant = '') {
  const query = variant ? `?variant=${variant}` : ''
  return `/api/hygiene/admin/attire/${row.employee_id}/shot${query}`
}

function standardUrl(variant = '') {
  return `/api/hygiene/admin/attire/standard${variant ? `?variant=${variant}` : ''}`
}

async function accept(row) {
  if (busyId.value) return
  busyId.value = row.employee_id
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/attire/${row.employee_id}/accept`)
    notice.value = `${personLabel(row)} 这张通过了。`
    await refresh(true)
  } catch (err) {
    // 别人刚处理过（或员工刚重拍）时服务端会拒 —— 刷新一下让页面跟上真实状态。
    errorText.value = err.message || '通过失败'
    await refresh(true)
  } finally {
    busyId.value = null
  }
}

function openReject(row) {
  rejectFor.value = row.employee_id
  rejectNote.value = ''
  errorText.value = ''
}

async function submitReject() {
  const employeeId = rejectFor.value
  if (!employeeId || busyId.value) return
  if (!rejectNote.value.trim()) {
    errorText.value = '请写明哪里不合格：员工要照着这句重拍。'
    return
  }
  busyId.value = employeeId
  errorText.value = ''
  try {
    await api.post(`/api/hygiene/admin/attire/${employeeId}/reject`, { note: rejectNote.value.trim() })
    notice.value = '已驳回，员工那一侧能看到原因。'
    rejectFor.value = null
    rejectNote.value = ''
    await refresh(true)
  } catch (err) {
    errorText.value = err.message || '驳回失败'
    await refresh(true)
  } finally {
    busyId.value = null
  }
}

/** 传/换标准图：进上传队列（断网也不丢），传完刷新这一屏。 */
function pickStandard(event) {
  const file = (event.target.files || [])[0]
  event.target.value = ''
  if (!file) return
  errorText.value = ''
  notice.value = ''
  try {
    const form = new FormData()
    form.append('file', file, file.name || 'standard.jpg')
    form.append('markup', '[]')
    imageUploads.enqueue({
      path: '/api/hygiene/admin/attire/standard',
      formData: form,
      label: '仪容仪表 · 标准图',
      detail: '照这个样子拍',
      onSuccess: () => refresh(true),
    })
    notice.value = '标准图已加入上传队列。'
  } catch (err) {
    errorText.value = err.message || '无法加入上传队列'
  }
}
</script>

<template>
  <div class="attire-page">
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Attire · 仪容仪表</p>
        <h1>今天谁要拍</h1>
        <p>
          今天排到班次的员工各拍一张，休假的与没排到的不在这张表上。这一项不设截止时间。
        </p>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="refresh()">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>
    <p v-if="notice" class="roster-notice" role="status">{{ notice }}</p>

    <div class="table-card standard-card">
      <div class="table-card-header">
        <h3>标准图 <span>{{ day.has_standard ? '已设置' : '还没传' }}</span></h3>
      </div>
      <div class="standard-body">
        <div v-if="day.has_standard" class="standard-shot">
          <a :href="standardUrl()" target="_blank" rel="noopener">
            <img :src="standardUrl('preview')" alt="仪容仪表标准图">
          </a>
          <p class="editor-lead">
            换过 {{ stamp(day.standard_updated_at) }}。点图看原图。
          </p>
        </div>
        <p v-else class="editor-lead">
          先传一张标准图：员工拍之前要照着它看（没传时他们那边拍不了）。
        </p>
        <label class="btn btn-primary upload-btn">
          {{ day.has_standard ? '换一张标准图' : '传标准图' }}
          <input type="file" accept="image/*" class="upload-input" @change="pickStandard">
        </label>
      </div>
    </div>

    <div class="table-card">
      <div class="table-card-header">
        <h3>今天的人 <span>{{ counts.required || 0 }}</span></h3>
      </div>
      <p class="count-line">
        等验收 {{ counts.pending || 0 }} · 待拍 {{ counts.todo || 0 }} ·
        已通过 {{ counts.passed || 0 }} · 已驳回 {{ counts.rejected || 0 }}
      </p>
      <div v-if="loading" class="roster-empty">正在加载…</div>
      <div v-else-if="!people.length" class="roster-empty">
        今天没有要拍的人（都休假或还没排班）。
      </div>
      <ul v-else class="attire-list">
        <li v-for="row in people" :key="row.employee_id" class="attire-row">
          <div class="shot-cell">
            <a v-if="row.has_shot" :href="shotUrl(row)" target="_blank" rel="noopener">
              <img :src="shotUrl(row, 'preview')" :alt="`${personLabel(row)} 的实拍`">
            </a>
            <span v-else class="shot-empty">还没交</span>
          </div>

          <div class="row-body">
            <p class="row-name">
              <strong>{{ personLabel(row) }}</strong>
              <span class="status-tag" :class="`is-${row.status}`">
                {{ STATUS_LABEL[row.status] || row.status }}
              </span>
            </p>
            <p v-if="row.note" class="roster-error">驳回原因：{{ row.note }}</p>
            <p v-if="row.updated_at" class="editor-lead">交于 {{ stamp(row.updated_at) }}</p>

            <div v-if="row.status === 'pending'" class="row-acts">
              <button
                type="button"
                class="btn btn-primary"
                :disabled="busyId === row.employee_id"
                @click="accept(row)"
              >通过</button>
              <button
                type="button"
                class="btn"
                :disabled="busyId === row.employee_id"
                @click="openReject(row)"
              >驳回</button>
            </div>

            <div v-if="rejectFor === row.employee_id" class="reject-box">
              <label :for="`reject-note-${row.employee_id}`">哪里不合格（必填，员工照着这句重拍）</label>
              <!-- 2026-10-05 用户裁定：驳回原因必填。这句提示就摆在输入框旁，
                   确认按钮在填之前是禁用的 —— 以前只有页面顶上那一行报错，
                   人在这一格上根本看不到为什么点不动。 -->
              <p class="reject-hint">必填 · 员工端原样显示这句话，他照着改</p>
              <textarea
                :id="`reject-note-${row.employee_id}`"
                v-model="rejectNote"
                class="staff-input"
                rows="2"
                maxlength="200"
                placeholder="写一句让他知道改什么，例如：帽子没戴正、围裙有污渍"
              ></textarea>
              <div class="row-acts">
                <button
                  type="button"
                  class="btn btn-primary"
                  :disabled="busyId === row.employee_id || !rejectNote.trim()"
                  @click="submitReject"
                >确认驳回</button>
                <button type="button" class="btn" @click="rejectFor = null">取消</button>
              </div>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.roster-notice {
  margin: 8px 0;
  padding: 8px 12px;
  border: 1px solid var(--hy-line);
  border-left: 3px solid var(--hy-accent, #c9a227);
  border-radius: 6px;
  color: var(--hy-faint);
  font-size: .84rem;
}
.standard-body {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 12px;
  flex-wrap: wrap;
}
.standard-shot img {
  display: block;
  width: 180px;
  border-radius: 8px;
  border: 1px solid var(--hy-line);
}
.count-line {
  margin: 0;
  padding: 8px 12px 0;
  color: var(--hy-faint);
  font-size: .82rem;
}
.attire-list {
  list-style: none;
  margin: 0;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.attire-row {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  border: 1px solid var(--hy-line);
  border-radius: 8px;
  padding: 10px;
}
.shot-cell img {
  display: block;
  width: 132px;
  border-radius: 6px;
}
.shot-empty {
  display: block;
  padding: 26px 8px;
  text-align: center;
  border: 1px dashed var(--hy-line);
  border-radius: 6px;
  color: var(--hy-faint);
  font-size: .78rem;
}
.row-name {
  margin: 0 0 4px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.status-tag {
  font-size: .72rem;
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid var(--hy-line);
}
.status-tag.is-pending { border-color: var(--hy-accent, #c9a227); }
.status-tag.is-passed { opacity: .7; }
.status-tag.is-rejected { border-color: var(--hy-danger, #c0392b); }
.row-acts {
  display: flex;
  gap: 8px;
  margin-top: 6px;
  flex-wrap: wrap;
}
.reject-box {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
/* 必填那句提示：贴着输入框、小一号（用的是这一页既有的报错色，不引新颜色）。 */
.reject-hint {
  margin: 0;
  font-size: .8rem;
  color: var(--hy-danger, #c0392b);
}
.upload-btn {
  position: relative;
  overflow: hidden;
  cursor: pointer;
}
.upload-input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
@media (max-width: 700px) {
  .attire-row { grid-template-columns: 1fr; }
  .shot-cell img { width: 100%; }
}
</style>
