<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import LuyunDatePicker from '../../components/ui/LuyunDatePicker.vue'
import HygieneImageLightbox from '../../components/hygiene/HygieneImageLightbox.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { formatHygieneStamp } from '../../utils/hygieneTime'

const KINDS = [
  { value: 'daily', label: '日常实拍' },
  { value: 'deep', label: '专项前后' },
  { value: 'fix', label: '整改原图' },
  { value: 'fix_reshoot', label: '整改回拍' },
  { value: 'teaching', label: '卫生教材' },
  { value: 'standard', label: '标准图版本' },
]
// 范围清理不含整改原图：整单删除是整改页的能力（ADR-0087），
// 免得「清旧照片」顺手删掉还没做完的工作单。
const PURGE_KINDS = KINDS.filter((kind) => kind.value !== 'fix')

const PAGE_SIZE = 24

const dateFrom = ref('')
const dateTo = ref('')
const selectedKinds = ref(KINDS.map((kind) => kind.value))
const zones = ref([])
const zoneId = ref('')

const records = ref([])
const total = ref(0)
const page = ref(1)
const truncated = ref(false)
const rangeLabel = ref('')
const loading = ref(true)
const errorText = ref('')

const storage = ref(null)
const storageHint = ref('')

const lightbox = ref(null)
const deleteTarget = ref(null)
const busy = ref(false)

const purgeOpen = ref(false)
const purgeKinds = ref(PURGE_KINDS.map((kind) => kind.value))

const exportJob = ref(null)
const exportError = ref('')
const exportHint = ref('')
let pollTimer = null

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const canPurge = computed(() => purgeKinds.value.length > 0 && !busy.value)

function isoDate(date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function defaultRange() {
  const to = new Date()
  const from = new Date(to.getTime() - 6 * 86400000)
  return { from: isoDate(from), to: isoDate(to) }
}

onMounted(async () => {
  const range = defaultRange()
  dateFrom.value = range.from
  dateTo.value = range.to
  await Promise.all([loadZones(), loadRecords(), loadStorage()])
})

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer)
})

useHygieneRealtime({
  id: 'hygiene-admin-data',
  resources: ['daily', 'deep', 'fix', 'teaching', 'zones'],
  // 只刷列表：存储概况要扫一遍照片目录，不该被别人的提交牵着反复扫。
  pull: loadRecords,
})

function queryParams() {
  return {
    kinds: selectedKinds.value.join(','),
    date_from: dateFrom.value || undefined,
    date_to: dateTo.value || undefined,
    zone_id: zoneId.value === '' ? undefined : zoneId.value,
  }
}

async function loadZones() {
  try {
    const data = await api.get('/api/hygiene/admin/zones')
    zones.value = data.zones || []
  } catch (err) {
    zones.value = []
  }
}

async function loadRecords() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/hygiene/admin/data/records', {
      ...queryParams(),
      page: page.value,
      page_size: PAGE_SIZE,
    })
    records.value = data.items || []
    total.value = data.total || 0
    truncated.value = Boolean(data.truncated)
    rangeLabel.value = `${data.date_from} ~ ${data.date_to}`
    if (page.value > pageCount.value) {
      page.value = pageCount.value
      await loadRecords()
    }
  } catch (err) {
    records.value = []
    total.value = 0
    errorText.value = err.message || '无法加载卫生数据'
  } finally {
    loading.value = false
  }
}

async function applyFilters() {
  if (!selectedKinds.value.length) {
    // 空类型在查询里等于"全部"，那是用户没表达清楚，不是他要全选。
    errorText.value = '请至少选一个类型再查询。'
    return
  }
  errorText.value = ''
  page.value = 1
  await loadRecords()
}

async function loadStorage() {
  try {
    storage.value = await api.get('/api/hygiene/admin/data/storage')
  } catch (err) {
    storage.value = null
  }
}

function photoUrl(captureId, variant = 'thumb') {
  return `/api/hygiene/admin/data/photo/${encodeURIComponent(captureId)}?variant=${variant}`
}

function openPhoto(record, photo) {
  lightbox.value = {
    src: photoUrl(photo.capture_id, 'preview'),
    alt: `${record.kind_label} · ${record.title}`,
  }
}

function formatBytes(bytes) {
  const value = Number(bytes || 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

async function confirmDelete() {
  const target = deleteTarget.value
  deleteTarget.value = null
  if (!target) return
  busy.value = true
  errorText.value = ''
  storageHint.value = ''
  try {
    const result = await api.delete(
      `/api/hygiene/admin/data/records/${target.kind}/${target.record_id}`,
    )
    storageHint.value = result.status_reset
      ? '已删除，这一项回到待拍，员工可以重拍。'
      : `已删除这条记录和它的 ${result.photos} 个照片文件。`
    await Promise.all([loadRecords(), loadStorage()])
  } catch (err) {
    errorText.value = err.message || '删除失败'
  } finally {
    busy.value = false
  }
}

async function confirmPurge() {
  purgeOpen.value = false
  busy.value = true
  errorText.value = ''
  storageHint.value = ''
  try {
    const result = await api.post('/api/hygiene/admin/data/purge', {
      kinds: purgeKinds.value,
      date_from: dateFrom.value || null,
      date_to: dateTo.value || null,
    })
    storageHint.value = `已清理 ${result.records} 条记录、${result.photos} 个照片文件（${result.date_from} ~ ${result.date_to}）。`
    await Promise.all([loadRecords(), loadStorage()])
  } catch (err) {
    errorText.value = err.message || '清理失败'
  } finally {
    busy.value = false
  }
}

async function startExport() {
  if (exportJob.value && exportJob.value.state === 'running') return
  exportError.value = ''
  exportHint.value = ''
  try {
    const job = await api.post(
      `/api/hygiene/admin/data/export/jobs?${new URLSearchParams(
        Object.entries(queryParams()).filter(([, value]) => value !== undefined),
      ).toString()}`,
    )
    exportJob.value = { ...job, done: 0, total: 0 }
    pollExport(job.job_id)
  } catch (err) {
    exportError.value = err.message || '无法开始导出'
  }
}

async function pollExport(jobId) {
  try {
    const job = await api.get(`/api/hygiene/admin/data/export/jobs/${jobId}`)
    exportJob.value = job
    if (job.state === 'running') {
      pollTimer = setTimeout(() => pollExport(jobId), 800)
      return
    }
    if (job.state === 'failed') {
      exportError.value = job.error || '导出失败'
      return
    }
    await api.download(`/api/hygiene/admin/data/export/jobs/${jobId}/download`, '卫生数据.zip')
    exportHint.value = `已打包 ${job.records || 0} 条记录、${job.count || 0} 张照片${
      job.skipped ? `（跳过 ${job.skipped} 张缺失文件）` : ''
    }。`
  } catch (err) {
    exportError.value = err.message || '导出失败'
  }
}

function gotoPage(next) {
  if (next < 1 || next > pageCount.value) return
  page.value = next
  return loadRecords()
}
</script>

<template>
  <div class="data-page">
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Archive · 数据与照片</p>
        <h1>数据与照片</h1>
        <p>按营业日翻看日常实拍、专项前后、整改原图与回拍、教材和标准图版本，导出台账，清理旧照片。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>营业日按 06:00 切，与员工端一致。删除是硬删除：记录和它的照片一起消失，红黑榜的历史计数不回滚。</p>
          <p>当前标准图、以及被任何一次提交当作对照用过的版本都不能删。</p>
        </details>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="loadRecords">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>
    <p v-if="storageHint" class="clocks-hint" role="status">{{ storageHint }}</p>

    <div v-if="storage" class="card storage-card">
      <div class="storage-total">
        <strong>{{ storage.total.count }}</strong>
        <span>张照片 · {{ formatBytes(storage.total.bytes) }}</span>
      </div>
      <ul class="storage-list">
        <li v-for="bucket in storage.kinds" :key="bucket.kind">
          <span>{{ bucket.label }}</span>
          <em>{{ bucket.count }} 张 · {{ formatBytes(bucket.bytes) }}</em>
        </li>
      </ul>
      <p v-if="storage.missing_files" class="clocks-hint">
        有 {{ storage.missing_files }} 张照片在库里但没有文件，取图会显示占位。
      </p>
      <p v-if="storage.orphan_files.count" class="clocks-hint">
        另有 {{ storage.orphan_files.count }} 个无主文件（{{ formatBytes(storage.orphan_files.bytes) }}），后台会自动清掉。
      </p>
    </div>

    <div class="card filter-card">
      <label class="clock-field">
        从
        <LuyunDatePicker v-model="dateFrom" dark placeholder="开始日期" aria-label="数据查询开始日期" />
      </label>
      <label class="clock-field">
        到
        <LuyunDatePicker v-model="dateTo" dark placeholder="结束日期" aria-label="数据查询结束日期" />
      </label>
      <label class="clock-field">
        责任区
        <select v-model="zoneId" class="input" aria-label="按责任区筛选">
          <option value="">全部</option>
          <option v-for="zone in zones" :key="zone.id" :value="zone.id">{{ zone.name }}</option>
        </select>
      </label>
      <fieldset class="kind-field">
        <legend>类型</legend>
        <label v-for="kind in KINDS" :key="kind.value" class="kind-option">
          <input v-model="selectedKinds" type="checkbox" :value="kind.value">
          <span>{{ kind.label }}</span>
        </label>
      </fieldset>
      <div class="filter-actions">
        <button type="button" class="btn btn-primary" :disabled="loading" @click="applyFilters">查询</button>
        <button
          type="button"
          class="btn"
          :disabled="
            loading
              || !selectedKinds.length
              || !records.length
              || (exportJob && exportJob.state === 'running')
          "
          @click="startExport"
        >
          导出台账
        </button>
      </div>
      <p v-if="exportJob && exportJob.state === 'running'" class="clocks-hint" role="status">
        正在打包 {{ exportJob.done || 0 }} / {{ exportJob.total || '…' }} 张…
      </p>
      <p v-if="exportHint" class="clocks-hint" role="status">{{ exportHint }}</p>
      <p v-if="exportError" class="roster-error" role="alert">{{ exportError }}</p>
    </div>

    <div class="table-card">
      <div class="table-card-header">
        <h3>记录 <span>{{ total }}</span></h3>
        <span v-if="rangeLabel" class="data-range">{{ rangeLabel }}</span>
      </div>
      <div v-if="loading" class="roster-empty">正在加载…</div>
      <div v-else-if="!records.length" class="roster-empty">这一段没有符合条件的记录。</div>
      <template v-else>
        <p v-if="truncated" class="clocks-hint">这一段记录太多，只显示了每个类型最近的部分，请收窄日期区间。</p>
        <ul class="data-list">
          <li v-for="record in records" :key="`${record.kind}-${record.record_id}`" class="data-row">
            <div class="data-photos">
              <button
                v-for="photo in record.photos"
                :key="photo.capture_id"
                type="button"
                class="data-thumb"
                :aria-label="`查看 ${record.title} 的${photo.role}`"
                @click="openPhoto(record, photo)"
              >
                <img :src="photoUrl(photo.capture_id)" :alt="`${record.title} ${photo.role}`" loading="lazy">
                <span>{{ photo.role }}</span>
              </button>
              <span v-if="!record.photos.length" class="data-nophoto">没有照片</span>
            </div>
            <div class="data-body">
              <strong>{{ record.title || '（无说明）' }}</strong>
              <span class="data-meta">
                {{ record.kind_label }}
                <template v-if="record.zone_name"> · {{ record.zone_name }}</template>
                <template v-if="record.subtitle"> · {{ record.subtitle }}</template>
              </span>
              <span class="data-meta">
                <template v-if="record.business_date">营业日 {{ record.business_date }} · </template>
                {{ formatHygieneStamp(record.occurred_at) }}
                · {{ record.submitter_name || record.submitter_phone || '—' }}
                · {{ record.status }}
              </span>
            </div>
            <div class="data-actions">
              <button
                type="button"
                class="btn btn-danger"
                :disabled="busy"
                @click="deleteTarget = record"
              >
                删除
              </button>
            </div>
          </li>
        </ul>
        <div class="data-pager">
          <button type="button" class="btn" :disabled="page <= 1 || loading" @click="gotoPage(page - 1)">上一页</button>
          <span>{{ page }} / {{ pageCount }}</span>
          <button type="button" class="btn" :disabled="page >= pageCount || loading" @click="gotoPage(page + 1)">下一页</button>
        </div>
      </template>
    </div>

    <div class="card danger-card">
      <div>
        <h3>按区间清理</h3>
        <p class="data-meta">
          删掉 {{ dateFrom || '最早' }} ~ {{ dateTo || '今天' }} 之间的记录与照片。这是硬删除，
          删了就找不回来；红黑榜的历史计数不回滚。
        </p>
      </div>
      <fieldset class="kind-field">
        <legend>清理哪些类型</legend>
        <label v-for="kind in PURGE_KINDS" :key="kind.value" class="kind-option">
          <input v-model="purgeKinds" type="checkbox" :value="kind.value">
          <span>{{ kind.label }}</span>
        </label>
      </fieldset>
      <button type="button" class="btn btn-danger" :disabled="!canPurge" @click="purgeOpen = true">
        清理这一段
      </button>
    </div>

    <ConfirmDialog
      v-if="deleteTarget"
      title="删除这条记录"
      :message="`删除「${deleteTarget.title || deleteTarget.kind_label}」（${deleteTarget.business_date}）以及它的照片。删了不能恢复；如果它还在等待验收，这一项会回到待拍。`"
      confirm-label="删除"
      danger
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />

    <ConfirmDialog
      v-if="purgeOpen"
      title="按区间清理"
      :message="`将删除 ${dateFrom || '最早'} ~ ${dateTo || '今天'} 之间选中的类型（${purgeKinds.length} 类），记录和照片一起删。这是硬删除，不能恢复。`"
      confirm-label="确认清理"
      danger
      @confirm="confirmPurge"
      @cancel="purgeOpen = false"
    />

    <HygieneImageLightbox
      v-if="lightbox"
      :src="lightbox.src"
      :alt="lightbox.alt"
      @close="lightbox = null"
    />
  </div>
</template>

<style scoped>
.storage-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.storage-total {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.storage-total strong {
  font-size: 28px;
  font-variant-numeric: tabular-nums;
}
.storage-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
}
.storage-list li {
  display: flex;
  gap: 6px;
  font-size: 13px;
  color: var(--hy-muted);
}
.storage-list em {
  font-style: normal;
  font-variant-numeric: tabular-nums;
}
.filter-card {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
}
.filter-actions {
  display: flex;
  gap: 8px;
}
.kind-field {
  border: 1px solid var(--hy-line, rgba(133, 205, 198, .28));
  border-radius: 8px;
  padding: 6px 10px 8px;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.kind-field legend {
  padding: 0 4px;
  font-size: 12px;
  color: var(--hy-muted);
}
.kind-option {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}
.data-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.data-row {
  display: grid;
  grid-template-columns: 220px 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 10px 14px;
  border-top: 1px solid var(--hy-line, rgba(133, 205, 198, .18));
}
.data-row:first-child { border-top: none; }
.data-photos {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.data-thumb {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 0;
  border: 1px solid var(--hy-line, rgba(133, 205, 198, .28));
  border-radius: 6px;
  background: none;
  cursor: pointer;
  overflow: hidden;
}
.data-thumb img {
  width: 72px;
  height: 54px;
  object-fit: cover;
  display: block;
}
.data-thumb span {
  font-size: 10px;
  color: var(--hy-muted);
  padding-bottom: 2px;
}
.data-nophoto { color: var(--hy-muted); font-size: 12px; }
.data-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.data-body strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.data-meta {
  color: var(--hy-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.data-range {
  color: var(--hy-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.data-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 12px;
  font-variant-numeric: tabular-nums;
}
.danger-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
  border-color: rgba(255, 120, 120, .35);
}
@media (max-width: 900px) {
  .data-row { grid-template-columns: 1fr; }
}
</style>
