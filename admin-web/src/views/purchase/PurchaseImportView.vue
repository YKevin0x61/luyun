<script setup>
/**
 * 厨房采购 · 数据迁移（仅管理端）。
 *
 * 把云开发控制台导出的 8 个集合搬到 luyun：先上传并自动跑 dry-run，管理员看过
 * 报告再确认写库。真正的映射、幂等和失败明细都在 services/purchase/importer.py，
 * 这里只负责上传、轮询与把报告摊开。
 */
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { api } from '../../api/client.js'
import PurchaseNav from './PurchaseNav.vue'
import { formatQty } from './purchaseUi.js'

const COLLECTIONS = [
  ['suppliers', '供应商'],
  ['categories', '分类'],
  ['products', '商品'],
  ['positions', '岗位'],
  ['date_factor_tags', '日期标签'],
  ['date_factor_weekday_defaults', '星期默认标签'],
  ['orders', '采购单'],
  ['material_stock', '盘点快照'],
]

const STAT_LABELS = {
  positions: '岗位',
  suppliers: '供应商',
  categories: '分类',
  products: '商品',
  date_factor_tags: '日期标签',
  date_factor_weekday_defaults: '星期默认',
  orders: '采购单',
  material_stock: '盘点快照',
}

const MAX_UPLOAD_MB = 100

const fileInput = ref(null)
const migration = ref(null)
const uploadBusy = ref(false)
const applying = ref(false)
const errorText = ref('')
const notice = ref('')
const confirmOpen = ref(false)
const options = reactive({
  sync_catalog: false,
  skip_orders: false,
  skip_stock: false,
  limit: 0,
})

let pollTimer = 0
let pollFailures = 0

const previewJob = computed(() => migration.value?.preview || null)
const applyJob = computed(() => migration.value?.apply || null)
const report = computed(() => applyJob.value?.report || previewJob.value?.report || null)
const reportIsApply = computed(() => Boolean(applyJob.value?.report))

const running = computed(() => {
  const states = [previewJob.value?.state, applyJob.value?.state]
  return states.some((state) => state === 'queued' || state === 'running')
})

const previewDone = computed(() => previewJob.value?.state === 'done')
const applyDone = computed(() => applyJob.value?.state === 'done')
const canApply = computed(
  () => previewDone.value && !running.value && !applyDone.value
)

const stageText = computed(() => {
  if (running.value) {
    const applyActive =
      applyJob.value?.state === 'queued' || applyJob.value?.state === 'running'
    const job = applyActive ? applyJob.value : previewJob.value
    return job?.stage_text || '处理中'
  }
  if (applyJob.value?.state === 'failed') return applyJob.value.error || '写库失败'
  if (previewJob.value?.state === 'failed') return previewJob.value.error || '预览失败'
  if (applyDone.value) return '写库完成'
  if (previewDone.value) return '预览完成，确认后写入'
  return ''
})

const jobTone = computed(() => {
  if (applyJob.value?.state === 'failed' || previewJob.value?.state === 'failed') return 'is-bad'
  if (applyDone.value) return 'is-ok'
  if (running.value) return 'is-busy'
  return ''
})

const collectionRows = computed(() =>
  COLLECTIONS.map(([key, label]) => {
    const info = migration.value?.collections?.[key] || {}
    return {
      key,
      label,
      missing: Boolean(info.missing),
      file: info.file || '',
      size: info.size || 0,
    }
  })
)

const statRows = computed(() => {
  if (!report.value?.stats) return []
  const rows = []
  for (const [key, label] of COLLECTIONS) {
    const stat = report.value.stats[key]
    if (!stat) continue
    rows.push({
      key,
      label: STAT_LABELS[key] || label,
      source: stat.source || 0,
      created: stat.created || 0,
      updated: stat.updated || 0,
      existing: stat.existing || 0,
      skipped: stat.skipped || 0,
      failed: stat.failed || 0,
    })
  }
  return rows
})

const totals = computed(() => {
  const result = { source: 0, created: 0, updated: 0, existing: 0, skipped: 0, failed: 0 }
  for (const row of statRows.value) {
    for (const key of Object.keys(result)) result[key] += row[key] || 0
  }
  return result
})

const warningCount = computed(() => report.value?.warnings?.length || 0)
const failureCount = computed(() => report.value?.failures?.length || 0)
const missingNames = computed(() =>
  collectionRows.value.filter((row) => row.missing).map((row) => row.label)
)
const uploadedNames = computed(() => (migration.value?.files || []).map((row) => row.name))
const reportJob = computed(() => applyJob.value || previewJob.value || null)

function pad2(value) {
  return String(value).padStart(2, '0')
}

function formatStamp(value) {
  if (!value) return '—'
  const date = new Date(Number(value) * 1000)
  if (Number.isNaN(date.getTime())) return '—'
  return (
    date.getFullYear() +
    '-' +
    pad2(date.getMonth() + 1) +
    '-' +
    pad2(date.getDate()) +
    ' ' +
    pad2(date.getHours()) +
    ':' +
    pad2(date.getMinutes())
  )
}

function formatBytes(value) {
  const size = Number(value) || 0
  if (size < 1024) return size + ' B'
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB'
  return (size / (1024 * 1024)).toFixed(1) + ' MB'
}

function fileError(files) {
  if (!files.length) return '请选择文件'
  const total = files.reduce((sum, file) => sum + file.size, 0)
  if (total > MAX_UPLOAD_MB * 1024 * 1024) {
    return '一次上传合计不能超过 ' + MAX_UPLOAD_MB + ' MB'
  }
  const zips = files.filter((file) => /\.zip$/i.test(file.name))
  if (zips.length) {
    if (files.length > 1) return 'zip 请一次只传一个，不要再混入散文件'
    return ''
  }
  const bad = files.find((file) => !/\.(json|jsonl|ndjson|gz)$/i.test(file.name))
  if (bad) return '只收 .json / .jsonl / .ndjson / .gz 或一个 .zip：' + bad.name
  return ''
}

async function uploadFiles(files) {
  const message = fileError(files)
  if (message) {
    errorText.value = message
    return
  }
  if (running.value) return
  uploadBusy.value = true
  errorText.value = ''
  notice.value = ''
  const form = new FormData()
  for (const file of files) form.append('files', file)
  form.append('sync_catalog', options.sync_catalog ? 'true' : 'false')
  form.append('skip_orders', options.skip_orders ? 'true' : 'false')
  form.append('skip_stock', options.skip_stock ? 'true' : 'false')
  form.append('limit', String(options.limit || 0))
  try {
    const data = await api.upload('/api/purchase/admin/migrations', form)
    migration.value = data.migration
    startPolling()
  } catch (err) {
    errorText.value = err?.message || '上传失败'
  } finally {
    uploadBusy.value = false
  }
}

async function onFileChange(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (files.length) await uploadFiles(files)
}

function chooseFiles() {
  if (!running.value) fileInput.value?.click()
}

function startPolling() {
  stopPolling()
  pollFailures = 0
  pollTimer = window.setInterval(refreshMigration, 1200)
  refreshMigration()
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = 0
  }
}

function settled(job) {
  return !job || job.state === 'done' || job.state === 'failed'
}

async function refreshMigration() {
  if (!migration.value?.id) return
  const migrationId = migration.value.id
  try {
    const data = await api.get('/api/purchase/admin/migrations/' + migrationId)
    if (!migration.value || migration.value.id !== migrationId) return
    migration.value = data.migration
    pollFailures = 0
    if (settled(data.migration.preview) && settled(data.migration.apply)) stopPolling()
  } catch (err) {
    pollFailures += 1
    if (pollFailures >= 4) {
      errorText.value = err?.message || '读取迁移进度失败'
      stopPolling()
    }
  }
}

async function previewAgain() {
  if (!migration.value?.id || running.value) return
  errorText.value = ''
  notice.value = ''
  try {
    const data = await api.post(
      '/api/purchase/admin/migrations/' + migration.value.id + '/preview',
      {}
    )
    migration.value = data.migration
    startPolling()
  } catch (err) {
    errorText.value = err?.message || '重新预览失败'
  }
}

function askApply() {
  if (!canApply.value) return
  confirmOpen.value = true
}

async function applyMigration() {
  if (!migration.value?.id || applying.value) return
  confirmOpen.value = false
  applying.value = true
  errorText.value = ''
  notice.value = ''
  try {
    const data = await api.post(
      '/api/purchase/admin/migrations/' + migration.value.id + '/apply',
      { confirm: true }
    )
    migration.value = data.migration
    startPolling()
  } catch (err) {
    errorText.value = err?.message || '写库失败'
  } finally {
    applying.value = false
  }
}

async function discardMigration() {
  if (!migration.value?.id || running.value) return
  try {
    await api.delete('/api/purchase/admin/migrations/' + migration.value.id)
    migration.value = null
    stopPolling()
    notice.value = '已丢弃这次上传'
  } catch (err) {
    errorText.value = err?.message || '丢弃失败'
  }
}

function downloadReport() {
  const job = reportJob.value
  if (!migration.value?.id || !job?.id) return
  api.download(
    '/api/purchase/admin/migrations/' + migration.value.id + '/jobs/' + job.id + '/report',
    'purchase-migration-' + job.kind + '.json'
  )
}

onBeforeUnmount(stopPolling)
</script>

<template>
  <div>
    <PurchaseNav active="migration" />

    <section class="mi-page">
      <header class="mi-head">
        <div class="mi-head-copy">
          <h1 class="mi-title">数据迁移</h1>
          <p class="mi-sub">
            把云开发导出的供应商、商品、岗位、采购单和盘点搬到 luyun。先预览、再写库；
            已存在的编码按规则更新，重复导入不会重复下单。
          </p>
        </div>
        <span class="mi-badge">仅管理端</span>
      </header>

      <div v-if="errorText" class="mi-banner is-bad" role="alert">
        {{ errorText }}
      </div>
      <div v-if="notice" class="mi-banner is-ok" role="status">
        {{ notice }}
      </div>

      <section class="mi-card">
        <div class="mi-card-head">
          <div>
            <h2 class="mi-card-title">1. 选择导出文件</h2>
            <p class="mi-card-hint">
              一个 zip，或 8 个集合的 JSON 文件；支持 .json / .jsonl / .ndjson / .gz。
              单次合计不超过 {{ MAX_UPLOAD_MB }} MB。
            </p>
          </div>
          <button
            v-if="migration"
            class="mi-btn"
            type="button"
            :disabled="running || uploadBusy"
            @click="chooseFiles"
          >
            换一份
          </button>
        </div>

        <label class="mi-drop" :class="{ 'is-busy': uploadBusy }">
          <input
            ref="fileInput"
            class="mi-file-input"
            type="file"
            multiple
            accept=".json,.jsonl,.ndjson,.gz,.zip"
            :disabled="uploadBusy || running"
            @change="onFileChange"
          />
          <span class="mi-drop-mark" aria-hidden="true">＋</span>
          <span class="mi-drop-title">
            {{ uploadBusy ? '正在上传…' : '点这里选择文件' }}
          </span>
          <span class="mi-drop-hint">
            文件名要以集合名开头，例如 suppliers.json、orders.json、material_stock.json
          </span>
        </label>

        <details class="mi-options">
          <summary class="mi-options-summary">导入选项</summary>
          <div class="mi-options-body">
            <label class="mi-check">
              <input v-model="options.sync_catalog" type="checkbox" :disabled="Boolean(migration) || uploadBusy" />
              <span>已存在的商品按源数据更新（默认只补缺）</span>
            </label>
            <label class="mi-check">
              <input v-model="options.skip_orders" type="checkbox" :disabled="Boolean(migration) || uploadBusy" />
              <span>不导入采购单</span>
            </label>
            <label class="mi-check">
              <input v-model="options.skip_stock" type="checkbox" :disabled="Boolean(migration) || uploadBusy" />
              <span>不导入盘点快照</span>
            </label>
            <label class="mi-limit">
              <span>每个集合只取前 N 条（0 = 全部，演练用）</span>
              <input v-model.number="options.limit" type="number" min="0" max="1000000" :disabled="Boolean(migration) || uploadBusy" />
            </label>
          </div>
        </details>
      </section>

      <section v-if="migration" class="mi-card">
        <div class="mi-card-head">
          <div>
            <h2 class="mi-card-title">2. 文件与集合</h2>
            <p class="mi-card-hint">
              上传于 {{ formatStamp(migration.created_at) }}，暂存到
              {{ formatStamp(migration.expires_at) }}（24 小时）。
            </p>
          </div>
          <button
            class="mi-btn is-danger"
            type="button"
            :disabled="running"
            @click="discardMigration"
          >
            丢弃
          </button>
        </div>

        <ul class="mi-file-list">
          <li v-for="name in uploadedNames" :key="name" class="mi-file-row">
            <span class="mi-file-name">{{ name }}</span>
          </li>
        </ul>
        <p v-if="migration.ignored && migration.ignored.length" class="mi-note">
          已忽略 {{ migration.ignored.length }} 个非集合文件：{{ migration.ignored.join('、') }}
        </p>

        <div class="mi-collections">
          <div
            v-for="row in collectionRows"
            :key="row.key"
            class="mi-collection"
            :class="{ 'is-missing': row.missing }"
          >
            <span class="mi-collection-name">{{ row.label }}</span>
            <span class="mi-collection-file">{{ row.missing ? '缺失' : row.file }}</span>
            <span v-if="!row.missing" class="mi-collection-size">{{ formatBytes(row.size) }}</span>
          </div>
        </div>
        <p v-if="missingNames.length" class="mi-note is-warn">
          缺 {{ missingNames.length }} 个集合（{{ missingNames.join('、') }}），这几块不会导入；
          已有的库数据不会被清空。
        </p>
      </section>

      <section v-if="migration" class="mi-card">
        <div class="mi-card-head">
          <div>
            <h2 class="mi-card-title">3. 预览与写库</h2>
            <p class="mi-card-hint">预览只读；确认写库后才会落表，可以重复执行。</p>
          </div>
          <span class="mi-stage" :class="jobTone">
            <span v-if="running" class="mi-spinner" aria-hidden="true"></span>
            {{ stageText }}
          </span>
        </div>

        <div class="mi-actions">
          <button
            class="mi-btn is-primary"
            type="button"
            :disabled="!canApply || applying"
            @click="askApply"
          >
            {{ applying ? '正在提交…' : applyDone ? '已写库' : '确认写库' }}
          </button>
          <button
            class="mi-btn"
            type="button"
            :disabled="running || !previewJob"
            @click="previewAgain"
          >
            重新预览
          </button>
          <button
            v-if="reportJob"
            class="mi-btn"
            type="button"
            :disabled="!reportJob.report"
            @click="downloadReport"
          >
            下载报告
          </button>
        </div>
        <p v-if="previewJob?.error" class="mi-error">{{ previewJob.error }}</p>
        <p v-if="applyJob?.error" class="mi-error">{{ applyJob.error }}</p>
      </section>

      <section v-if="report" class="mi-card">
        <div class="mi-card-head">
          <div>
            <h2 class="mi-card-title">4. {{ reportIsApply ? '写库结果' : '预览结果' }}</h2>
            <p class="mi-card-hint">
              {{ reportIsApply ? '已经写入目标库。' : '只读预览，还没有写入。' }}
              共 {{ totals.created }} 条新建、{{ totals.updated }} 条更新、{{ totals.failed }} 条失败。
            </p>
          </div>
          <span class="mi-badge" :class="reportIsApply ? 'is-ok' : ''">
            {{ reportIsApply ? '已写库' : 'dry-run' }}
          </span>
        </div>

        <div class="mi-totals">
          <div class="mi-total">
            <span class="mi-total-value">{{ totals.created }}</span>
            <span class="mi-total-label">新建</span>
          </div>
          <div class="mi-total">
            <span class="mi-total-value">{{ totals.updated }}</span>
            <span class="mi-total-label">更新</span>
          </div>
          <div class="mi-total">
            <span class="mi-total-value">{{ totals.existing }}</span>
            <span class="mi-total-label">已存在</span>
          </div>
          <div class="mi-total">
            <span class="mi-total-value">{{ totals.skipped }}</span>
            <span class="mi-total-label">跳过</span>
          </div>
          <div class="mi-total" :class="{ 'is-bad': totals.failed }">
            <span class="mi-total-value">{{ totals.failed }}</span>
            <span class="mi-total-label">失败</span>
          </div>
        </div>

        <div class="mi-table-wrap">
          <table class="mi-table">
            <thead>
              <tr>
                <th>集合</th>
                <th>源</th>
                <th>新建</th>
                <th>更新</th>
                <th>已存在</th>
                <th>跳过</th>
                <th>失败</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in statRows" :key="row.key">
                <td>{{ row.label }}</td>
                <td>{{ row.source }}</td>
                <td>{{ row.created }}</td>
                <td>{{ row.updated }}</td>
                <td>{{ row.existing }}</td>
                <td>{{ row.skipped }}</td>
                <td :class="{ 'is-bad': row.failed }">{{ row.failed }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <p v-if="report.orders?.items_imported" class="mi-note">
          订单项 {{ report.orders.items_imported }} 条，数量合计
          {{ formatQty(report.orders.quantity_imported) }}；跨岗位订单
          {{ report.orders.multi_position_orders }} 单。
        </p>

        <details v-if="warningCount" class="mi-log">
          <summary class="mi-log-summary">警告 {{ warningCount }} 条</summary>
          <ul class="mi-log-list">
            <li v-for="(item, index) in report.warnings" :key="'w' + index">{{ item }}</li>
          </ul>
        </details>
        <details v-if="failureCount" class="mi-log is-bad" open>
          <summary class="mi-log-summary">失败 {{ failureCount }} 条（逐行看原因）</summary>
          <ul class="mi-log-list">
            <li v-for="(item, index) in report.failures" :key="'f' + index">{{ item }}</li>
          </ul>
        </details>
      </section>

      <p class="mi-foot">
        迁移是管理端一次性操作；源数据仍是云开发里的原件，网页只写入 luyun 的
        purchase_* 表，不删除、不改云端数据。
      </p>
    </section>

    <Teleport to="body">
      <div v-if="confirmOpen" class="mi-scrim" @click.self="confirmOpen = false">
        <div class="mi-dialog" role="dialog" aria-modal="true" aria-labelledby="mi-confirm-title">
          <h2 id="mi-confirm-title" class="mi-dialog-title">确认写库</h2>
          <p class="mi-dialog-text">
            会按上面的预览结果写入目标库：新建 {{ totals.created }} 条、更新
            {{ totals.updated }} 条、预计失败 {{ totals.failed }} 条。重复执行会按编码和
            订单号幂等，不会重复下单。
          </p>
          <p class="mi-dialog-text is-warn">写库过程中不要关闭这个页面。</p>
          <div class="mi-dialog-actions">
            <button class="mi-btn" type="button" @click="confirmOpen = false">取消</button>
            <button class="mi-btn is-primary" type="button" @click="applyMigration">
              确认写库
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.mi-page {
  width: 100%;
  min-width: 0;
  max-width: 920px;
  margin: 0 auto;
  padding: 10px 12px 32px;
}
.mi-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 14px;
}
.mi-head-copy {
  min-width: 0;
}
.mi-title {
  margin: 0;
  font-size: var(--fs-24);
  line-height: 1.25;
}
.mi-sub {
  margin: 5px 0 0;
  font-size: var(--fs-13);
  line-height: 1.55;
  opacity: 0.72;
}
.mi-badge {
  flex: 0 0 auto;
  padding: 4px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-2);
  font-size: var(--fs-12);
  white-space: nowrap;
}
.mi-badge.is-ok {
  border-color: var(--wb-ok-a10);
  background: var(--wb-ok-a10);
}
.mi-card {
  min-width: 0;
  margin: 0 0 14px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow-sm);
}
.mi-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 12px;
}
.mi-card-title {
  margin: 0;
  font-size: var(--fs-16);
  line-height: 1.35;
}
.mi-card-hint {
  margin: 4px 0 0;
  font-size: var(--fs-12);
  line-height: 1.5;
  opacity: 0.7;
}
.mi-banner {
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  font-size: var(--fs-13);
  line-height: 1.5;
}
.mi-banner.is-bad {
  border-color: var(--danger-bright);
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
}
.mi-banner.is-ok {
  background: var(--wb-ok-a10);
}
.mi-drop {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 116px;
  padding: 16px;
  border: 1px dashed var(--line-strong);
  border-radius: 12px;
  background: var(--surface-2);
  cursor: pointer;
  text-align: center;
}
.mi-drop.is-busy {
  opacity: 0.7;
}
.mi-drop:focus-within {
  outline: 2px solid var(--action);
  outline-offset: 2px;
}
.mi-file-input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}
.mi-file-input:disabled {
  cursor: not-allowed;
}
.mi-drop-mark {
  display: block;
  font-size: var(--fs-24);
  line-height: 1;
  color: var(--action);
}
.mi-drop-title {
  font-size: var(--fs-14);
  font-weight: 600;
}
.mi-drop-hint {
  max-width: 520px;
  font-size: var(--fs-12);
  line-height: 1.5;
  opacity: 0.7;
}
.mi-options {
  margin: 12px 0 0;
}
.mi-options-summary {
  display: flex;
  align-items: center;
  min-height: 44px;
  font-size: var(--fs-13);
  cursor: pointer;
}
.mi-options-body {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-2);
}
.mi-check {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-height: 44px;
  padding: 8px 0;
  font-size: var(--fs-13);
  line-height: 1.45;
}
.mi-check input {
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  margin-top: 2px;
}
.mi-limit {
  display: grid;
  gap: 6px;
  font-size: var(--fs-12);
}
.mi-limit input {
  min-height: 44px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-1);
  color: var(--ink);
  font-size: var(--fs-14);
}
.mi-file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0 0 10px;
  padding: 0;
  list-style: none;
}
.mi-file-row {
  max-width: 100%;
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
}
.mi-file-name {
  display: block;
  overflow: hidden;
  font-size: var(--fs-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mi-collections {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}
.mi-collection {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px 8px;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
}
.mi-collection.is-missing {
  border-color: var(--danger-bright);
  background: var(--wb-warn-a10);
}
.mi-collection-name {
  font-size: var(--fs-13);
  font-weight: 600;
}
.mi-collection-file {
  grid-column: 1 / -1;
  overflow: hidden;
  font-size: var(--fs-11);
  opacity: 0.7;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mi-collection-size {
  font-size: var(--fs-11);
  opacity: 0.6;
}
.mi-note {
  margin: 10px 0 0;
  font-size: var(--fs-12);
  line-height: 1.55;
  opacity: 0.72;
}
.mi-note.is-warn {
  color: var(--danger-bright);
  opacity: 1;
}
.mi-stage {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 52%;
  padding: 5px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-2);
  font-size: var(--fs-12);
  line-height: 1.35;
  text-align: right;
}
.mi-stage.is-ok {
  border-color: var(--wb-ok-a10);
  background: var(--wb-ok-a10);
}
.mi-stage.is-bad {
  border-color: var(--danger-bright);
  background: var(--wb-danger-a10);
  color: var(--danger-bright);
}
.mi-spinner {
  width: 10px;
  height: 10px;
  border: 2px solid var(--line-strong);
  border-top-color: var(--action);
  border-radius: 50%;
  animation: mi-spin 0.8s linear infinite;
}
@keyframes mi-spin {
  to {
    transform: rotate(360deg);
  }
}
.mi-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.mi-btn {
  min-height: 44px;
  padding: 8px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: var(--surface-1);
  color: var(--ink);
  font-size: var(--fs-14);
  line-height: 1.3;
  cursor: pointer;
}
.mi-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.mi-btn.is-primary {
  border-color: var(--action);
  background: var(--action);
  color: var(--action-ink);
  font-weight: 600;
}
.mi-btn.is-danger {
  border-color: var(--danger-bright);
  color: var(--danger-bright);
}
.mi-btn:focus-visible {
  outline: 2px solid var(--action);
  outline-offset: 2px;
}
.mi-error {
  margin: 10px 0 0;
  font-size: var(--fs-13);
  line-height: 1.5;
  color: var(--danger-bright);
}
.mi-totals {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 6px;
  margin: 0 0 12px;
}
.mi-total {
  display: grid;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--surface-2);
}
.mi-total.is-bad {
  border-color: var(--danger-bright);
  background: var(--wb-danger-a10);
}
.mi-total-value {
  font-size: var(--fs-20);
  font-weight: 700;
  line-height: 1.15;
}
.mi-total-label {
  font-size: var(--fs-11);
  opacity: 0.7;
}
.mi-table-wrap {
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 9px;
}
.mi-table {
  width: 100%;
  min-width: 520px;
  border-collapse: collapse;
  font-size: var(--fs-13);
}
.mi-table th,
.mi-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  white-space: nowrap;
}
.mi-table th {
  background: var(--surface-2);
  font-size: var(--fs-12);
  font-weight: 600;
}
.mi-table tr:last-child td {
  border-bottom: 0;
}
.mi-table td.is-bad {
  color: var(--danger-bright);
  font-weight: 600;
}
.mi-log {
  margin: 12px 0 0;
}
.mi-log-summary {
  display: flex;
  align-items: center;
  min-height: 44px;
  font-size: var(--fs-13);
  font-weight: 600;
  cursor: pointer;
}
.mi-log.is-bad .mi-log-summary {
  color: var(--danger-bright);
}
.mi-log-list {
  max-height: 260px;
  margin: 0;
  padding: 8px 10px 8px 26px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
  font-size: var(--fs-12);
  line-height: 1.55;
}
.mi-log-list li {
  margin: 3px 0;
  overflow-wrap: anywhere;
}
.mi-foot {
  margin: 18px 0 0;
  font-size: var(--fs-11);
  line-height: 1.6;
  opacity: 0.6;
}
.mi-scrim {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 18px;
  background: var(--wb-scrim-strong);
}
.mi-dialog {
  width: 100%;
  max-width: 440px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow);
}
.mi-dialog-title {
  margin: 0 0 8px;
  font-size: var(--fs-20);
}
.mi-dialog-text {
  margin: 0 0 10px;
  font-size: var(--fs-13);
  line-height: 1.6;
}
.mi-dialog-text.is-warn {
  color: var(--danger-bright);
}
.mi-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}

@media (max-width: 720px) {
  .mi-card-head {
    flex-wrap: wrap;
  }
  .mi-stage {
    max-width: 100%;
    text-align: left;
  }
  .mi-totals {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .mi-head {
    flex-wrap: wrap;
  }
  .mi-collections {
    grid-template-columns: 1fr;
  }
  .mi-totals {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 430px) {
  .mi-page {
    padding: 8px 10px 28px;
  }
  .mi-card {
    padding: 12px;
  }
  .mi-totals {
    grid-template-columns: 1fr;
  }
  .mi-actions .mi-btn {
    flex: 1 1 100%;
  }
}
</style>
