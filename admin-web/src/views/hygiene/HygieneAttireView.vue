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
    <header class="wbx-head">
      <div class="wbx-head-main">
        <p class="wbx-kicker">ATTIRE · 仪容仪表</p>
        <h1 class="wbx-title">今天谁要拍</h1>
      </div>
      <div class="wbx-pills">
        <button type="button" class="btn" :disabled="loading" @click="refresh()">刷新</button>
      </div>
      <p class="wbx-sub">今天排到班次的员工各拍一张，休假的与没排到的不在这张表上。这一项不设截止时间。</p>
      <details class="rule-help">
        <summary>规则说明</summary>
        <p>名单来自排班：休假的与今天没排到的人不在这张表上。驳回必须写原因，员工照着那句话重拍。这一项不设截止时间。</p>
      </details>
    </header>

    <p v-if="errorText" class="wbx-error" role="alert">{{ errorText }}</p>
    <p v-if="notice" class="wbx-notice" role="status">{{ notice }}</p>

    <div class="wbx-readline">
      <span class="wbx-read"><span>等验收</span><b>{{ counts.pending || 0 }}</b></span>
      <span class="wbx-read"><span>待拍</span><b>{{ counts.todo || 0 }}</b></span>
      <span class="wbx-read"><span>已通过</span><b>{{ counts.passed || 0 }}</b></span>
      <span class="wbx-read"><span>已驳回</span><b>{{ counts.rejected || 0 }}</b></span>
      <span class="wbx-read is-unit"><em>应拍 {{ counts.required || 0 }} 人</em></span>
    </div>

    <div class="wbx-cols">
      <section class="wbx-panel is-standard">
        <header class="wbx-ph">
          <i class="led sig"></i>
          <h2>标准图</h2>
          <span class="wbx-hint">{{ day.has_standard ? '已设置' : '还没传' }}</span>
        </header>
        <div class="standard-body">
          <div v-if="day.has_standard" class="standard-shot">
            <a :href="standardUrl()" target="_blank" rel="noopener">
              <img :src="standardUrl('preview')" alt="仪容仪表标准图">
            </a>
            <p class="wbx-sub">换过 {{ stamp(day.standard_updated_at) }}。点图看原图。</p>
          </div>
          <p v-else class="wbx-sub">
            先传一张标准图：员工拍之前要照着它看（没传时他们那边拍不了）。
          </p>
          <label class="btn btn-primary upload-btn">
            {{ day.has_standard ? '换一张标准图' : '传标准图' }}
            <input type="file" accept="image/*" class="upload-input" @change="pickStandard">
          </label>
        </div>
      </section>

      <section v-if="loading || people.length" class="wbx-panel">
        <header class="wbx-ph">
          <i class="led warn"></i>
          <h2>今天的人</h2>
          <span class="wbx-hint">应拍 {{ counts.required || 0 }} 人</span>
        </header>
        <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
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
                <span class="wbx-state" :class="`is-${row.status}`">
                  <i class="led" :class="{ ok: row.status === 'passed', warn: row.status === 'pending' || row.status === 'todo', bad: row.status === 'rejected' }"></i>
                  {{ STATUS_LABEL[row.status] || row.status }}
                </span>
                <span v-if="row.updated_at" class="row-time">交于 {{ stamp(row.updated_at) }}</span>
              </p>
              <p v-if="row.note" class="roster-error">驳回原因：{{ row.note }}</p>

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
      </section>
      <p v-else class="wbx-empty-bar">今天没有要拍的人（都休假或还没排班）。</p>
    </div>
  </div>
</template>

<style scoped>
/* B 案 v2 版式 + v3 布局纪律（2026-10-17 审查）：一条读数行、页头右手只留动作、
   两栏比例按内容（标准图 320 / 队列给大）、行高压到 72、空态收成横条。 */
.attire-page {
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
.wbx-notice { margin: 0; font-size: 11px; color: var(--ink-dim); }
.led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.led.ok { background: var(--ok); }
.led.warn { background: var(--warn); }
.led.bad { background: var(--danger); }
.led.sig { background: var(--action); }

/* 一条读数行：标签 + mono 数字 + 单位同排（不放重复的页头胶囊、不画纯计数 gauge） */
.wbx-readline {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 26px;
  padding: 10px 14px;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08);
}
.wbx-read { display: inline-flex; align-items: baseline; gap: 6px; }
.wbx-read span { font: 500 9.5px/1 var(--font-sans); letter-spacing: .18em; color: var(--ink-faint); }
.wbx-read b { font: 500 20px/1 var(--font-mono); color: var(--ink); }
.wbx-read em { font-style: normal; font-size: 9.5px; color: var(--ink-faint); }

.wbx-cols { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 14px; align-items: stretch; }
.wbx-panel {
  display: flex; flex-direction: column;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08); overflow: hidden;
}
.wbx-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbx-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbx-hint { margin-left: auto; font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }

/* 空态横条：一行字、不超过 64px，不带面板头 */
.wbx-empty-bar {
  align-self: start;
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; margin: 0; padding: 10px 14px;
  font-size: 10.5px; color: var(--ink-faint);
  background: var(--surface-1); border: 1px dashed var(--line-strong); border-radius: var(--r-1);
}

.standard-body { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px 14px; }
.standard-shot { display: flex; flex-direction: column; gap: 8px; }
.standard-shot img { display: block; width: 100%; max-width: 220px; border-radius: var(--r-1); border: 1px solid var(--line); }
.standard-body .wbx-sub { margin: 0; font-size: 10.5px; }
.standard-body .upload-btn { position: relative; overflow: hidden; cursor: pointer; align-self: flex-start; white-space: nowrap; }
.upload-input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }

.attire-list { list-style: none; margin: 0; padding: 0; flex: 1 1 auto; }
.attire-row {
  display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 12px; align-items: start;
  min-height: 72px; padding: 8px 14px; border-bottom: 1px solid var(--wb-ink-a08);
}
.attire-row:last-child { border-bottom: 0; }
.attire-row:hover { background: var(--surface-2); }
.shot-cell img { display: block; width: 56px; height: 56px; object-fit: cover; border-radius: var(--r-1); border: 1px solid var(--line); }
.shot-empty {
  display: grid; place-items: center; width: 56px; height: 56px;
  border: 1px dashed var(--line-strong); border-radius: var(--r-1);
  font-size: 9px; letter-spacing: .06em; color: var(--ink-faint); text-align: center;
}
.row-body { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.row-name { margin: 0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.row-name strong { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.row-time { font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbx-state { display: inline-flex; align-items: center; gap: 5px; font-size: 9px; letter-spacing: .06em; color: var(--ink-dim); }
.wbx-state.is-pending { color: var(--warn); }
.wbx-state.is-todo { color: var(--ink-faint); }
.wbx-state.is-passed { color: var(--ok); }
.wbx-state.is-rejected { color: var(--danger-bright); }
.row-acts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 2px; }
.reject-box { margin-top: 4px; display: flex; flex-direction: column; gap: 6px; }
.reject-box label { font-size: 10px; color: var(--ink-dim); }
.reject-hint { margin: 0; font-size: 10px; color: var(--danger-bright); }
.attire-page :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }
@media (max-width: 900px) {
  .wbx-cols { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .wbx-head { grid-template-columns: minmax(0, 1fr); }
  .wbx-pills { grid-column: 1; grid-row: 2; justify-self: start; margin-top: 10px; }
  .wbx-sub { grid-row: 3; }
  .wbx-head .rule-help { grid-row: 4; }
  .wbx-title { font-size: 22px; }
  /* 390：不再用 1fr 大图，固定 64px 缩略图 + 姓名 / 时间 / 动作 */
  .attire-row { grid-template-columns: 64px minmax(0, 1fr); padding: 8px 12px; }
}
</style>
